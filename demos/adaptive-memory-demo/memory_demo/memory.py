from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .config import Settings
from .database import DEMO_USER_ID, Database, iso, utc_now
from .schemas import MemoryCandidate, MemoryConsolidation


@dataclass(frozen=True)
class LifecyclePolicy:
    half_life_days: int
    review_after_days: int
    expire_after_days: int


# Product defaults for the demo, not universal truths. Production values should
# be calibrated against real correction and lifecycle data.
LIFECYCLE_POLICIES: dict[str, LifecyclePolicy] = {
    "stable": LifecyclePolicy(half_life_days=540, review_after_days=365, expire_after_days=1_095),
    "slow": LifecyclePolicy(half_life_days=180, review_after_days=180, expire_after_days=540),
    "changing": LifecyclePolicy(half_life_days=45, review_after_days=45, expire_after_days=180),
    "ephemeral": LifecyclePolicy(half_life_days=7, review_after_days=7, expire_after_days=30),
}

_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
_KEY_RE = re.compile(r"[^a-z0-9_.-]+")
_SECRET_RE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}|"
    r"\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"\b(?:password|passcode|secret|api[_ -]?key|access[_ -]?token)"
    r"\s*(?::|=|\bis\b)\s*\S+|"
    r"\b(?:passwort|kennwort|contraseña|secreto|mot de passe|secret|पासवर्ड|गुप्त|"
    r"كلمة المرور|سر|パスワード|秘密|parola d['’]ordine|segreto|senha|segredo|"
    r"пароль|секрет)\b\s*(?::|=|ist|es|est|है|هي|هو|は|è|é|это)\s*\S+|"
    r"\b(?:api[- ]?(?:schlüssel|clave|clé|chiave|chave|ключ)|"
    r"(?:schlüssel|clave|clé|chiave|chave|ключ)\s*api|एपीआई कुंजी|مفتاح api)\b"
    r"\s*(?::|=|-|—|ist|lautet|es|est|है|هي|هو|è|é|это)\s*\S+|"
    r"(?:密码|密钥|令牌|APIキー|アクセストークン)\s*(?:是|は|[:：=])\s*\S+)",
    re.IGNORECASE,
)
_INSTRUCTION_RE = re.compile(
    r"\b(ignore (?:all |the )?(?:previous|prior) instructions|system prompt|developer message|"
    r"call (?:a |the )?tool|tool call|bypass (?:the )?policy|reveal (?:the )?prompt)\b",
    re.IGNORECASE,
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[._-])(health|medical|diagnosis|finance|income|salary|religion|politic|"
    r"sexual|address|coordinates?|biometric|disability)(?:$|[._-])",
    re.IGNORECASE,
)
_SENSITIVE_CONTENT_RE = re.compile(
    r"(?:\b\d{1,6}\s+[\w.'-]+(?:\s+[\w.'-]+){0,3}\s+"
    r"(?:street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|drive|dr|way)\b|"
    r"\b(?:calle|rue|via|rua|улица|проспект|[\w.'-]*(?:straße|strasse|weg|allee|platz))\b"
    r"[^\n]{0,50}\d{1,6}\b|"
    r"(?:地址|住所|पता|عنوان)[^\n]{0,50}\d|"
    r"(?:都|道|府|県|市|区|町|村)[^\n]{0,30}\d+(?:[-－]\d+){1,}|"
    r"(?:شارع|طريق)[^\n]{0,50}\d|"
    r"\b\d{1,6}\s+(?:rue|calle|via|rua|улица|проспект)\b|\d+丁目|"
    r"\b(?:latitude|longitude|gps|coordinates?|diagnos(?:is|ed)|medical|salary|income|"
    r"diagnose|medizinisch|gehalt|einkommen|diagnóstico|médic[oa]|salario|ingresos|"
    r"diagnostic|médical|salaire|revenu|diagnosi|medic[oa]|stipendio|reddito|"
    r"diagnóstico|salário|renda|диагноз|медицин|зарплат|доход)\b|"
    r"诊断|医疗|工资|收入|निदान|चिकित्सा|वेतन|आय|تشخيص|طبي|راتب|دخل|診断|医療|給与|収入)",
    re.IGNORECASE,
)
_NON_SELF_CONTEXT_RE = re.compile(
    r"(?:[\"“”]\s*(?:i\b|i['’]m\b|my\b)|"
    r"\b(?:example|sample|quoted|pasted|form|document)\s*(?:text)?\s*:|"
    r"\b(?:said|says|wrote|states)\s*[,:'\"“”]|"
    r"\b(?:beispiel|formular|ejemplo|formulario|exemple|formulaire|esempio|modulo|"
    r"exemplo|formulário|пример|форма)\s*:|示例|例文|مثال|उदाहरण)",
    re.IGNORECASE,
)
_FORGET_INTENT_RE = re.compile(
    r"(?:^\s*(?:(?:(?:please|kindly|can you|could you|i want you to|bitte|por favor|"
    r"s['’]il vous plaît|per favore|من فضلك|يرجى|कृपया|пожалуйста)\s*[,]?\s+)|请\s*)?"
    r"(?:(?:forget|delete|remove|erase|vergiss|lösche|entferne|olvida|borra|elimina|"
    r"oublie|supprime|efface|dimentica|rimuovi|esqueça|apague|remova)\b|"
    r"忘记|削除|别记|भूल|हटा|याद मत|انس|احذف|لا تتذكر|забудь|удали|не запоминай)|"
    r"忘れてください)",
    re.IGNORECASE,
)
_CONFIRM_INTENT_RE = re.compile(
    r"(?:\b(?:still true|still correct|remains true|stimmt noch|sigue siendo cierto|"
    r"toujours vrai|ancora vero|ainda é verdade)\b|仍然正确|まだ正しい|"
    r"अभी भी सही|لا يزال صحيح|всё ещё верно)",
    re.IGNORECASE,
)
_LOCATION_DELETE_RE = re.compile(
    r"(?:where i live|my (?:home|location|city)|wo ich wohne|dónde vivo|où j[’']habite|"
    r"dove vivo|onde moro|где я живу|我住在哪里|居住地|कहाँ रह|أين أعيش|مدينتي)",
    re.IGNORECASE,
)
_NAME_DELETE_RE = re.compile(
    r"(?:my name|mein name|mi nombre|mon nom|il mio nome|meu nome|мо[её] имя|"
    r"我的名字|名前|मेरा नाम|اسمي)",
    re.IGNORECASE,
)
_KIND_PREFIXES: dict[str, frozenset[str]] = {
    "identity": frozenset({"identity"}),
    "preference": frozenset({"preference"}),
    "project": frozenset({"project"}),
    "relationship": frozenset({"relationship"}),
    "constraint": frozenset({"constraint"}),
    "experience": frozenset({"experience"}),
    "goal": frozenset({"goal"}),
    "other": frozenset({"other"}),
}
_MIN_MODEL_CONFIDENCE = 0.65
_TOKEN_ALIASES = {
    "cities": "location",
    "city": "location",
    "home": "location",
    "where": "location",
    "lived": "live",
    "lives": "live",
    "living": "live",
    "named": "name",
    "names": "name",
    "preferences": "preference",
    "preferred": "preference",
    "prefers": "preference",
    "projects": "project",
}
_CONCEPT_PATTERNS: dict[str, re.Pattern[str]] = {
    "location": re.compile(
        r"\b(?:city|cities|home|where|live|location|mov\w*|wo|wohn\w*|leb\w*|"
        r"stadt|zieh\w*|gezogen|dónde|vivo|vivir|ciudad|mud\w*|où|habit\w*|ville|"
        r"déménag\w*|dove|città|trasfer\w*|onde|moro|cidade|mudei|где|жив\w*|"
        r"город|переех\w*|कहाँ|रह\w*|चली|أين|و?أعيش|و?أسكن|مدينة|مدينتي|و?انتقلت)\b|"
        r"哪里|哪儿|住|城市|搬|どこ|住ん|居住地|引っ越",
        re.IGNORECASE,
    ),
    "name": re.compile(
        r"\b(?:name|called|hei(?:ß|ss)e|nombre|llamo|nom|appelle|nome|chiamo|"
        r"имя|зовут|नाम|اسمي|اسم)\b|我叫|名字|姓名|名前",
        re.IGNORECASE,
    ),
    "preference": re.compile(
        r"\b(?:prefer\w*|favorite|favourite|bevorzug\w*|lieblings\w*|prefier\w*|"
        r"favorit\w*|préfér\w*|préféré\w*|preferit\w*|preferid\w*|люб\w*|"
        r"предпочит\w*|पसंद|أفضل|أفضّل)\b|偏好|喜欢|好み|好き",
        re.IGNORECASE,
    ),
    "project": re.compile(
        r"\b(?:project|building|working on|projekt|baue|proyecto|construy\w*|"
        r"projet|construis\w*|progetto|costru\w*|projeto|constru\w*|проект|"
        r"стро\w*|परियोजना|बना\w*|مشروع|أبني)\b|项目|制作|作って|プロジェクト",
        re.IGNORECASE,
    ),
}
_SLOT_VALUE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "identity.name": (
        re.compile(
            r"\bmy name is\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:and\s+)?i\s+(?:live|work|prefer|like|love)\b|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:ich heiße|ich heisse|mein name ist)\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:und\s+)?(?:ich\s+)?(?:wohne|lebe|arbeite)\b|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:me llamo|mi nombre es)\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:y\s+)?(?:yo\s+)?(?:vivo|trabajo|prefiero)\b|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:je m['’]appelle|mon nom est)\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:et\s+)?(?:j['’]habite|je vis|je travaille)\b|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:mi chiamo|il mio nome è)\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:e\s+)?(?:vivo|abito|lavoro)\b|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:me chamo|meu nome é)\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:e\s+)?(?:moro|vivo|trabalho)\b|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bменя зовут\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:и\s+)?я\s+(?:живу|работаю)\b|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(r"मेरा नाम\s+(?P<value>[^।,.!?]+?)\s+है", re.IGNORECASE),
        re.compile(
            r"(?:^|\s)اسمي\s+(?P<value>[^،.!؟?؛;]+?)"
            r"(?=\s+و?(?:أعيش|اعيش|أسكن|اسكن|أعمل)\b|[،,.!؟?؛;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:我叫|我的名字是)\s*(?P<value>[^，。！？；,.!?;]+?)"
            r"(?=(?:并且|而且)?我住在|[，。！？；,.!?;]|$)"
        ),
        re.compile(
            r"私の名前は\s*(?P<value>[^、。！？,.!?;]+?)"
            r"(?=\s*(?:です|で(?=[、,]|私は)|私は|[。.!?]|$))"
        ),
    ),
    "identity.location": (
        re.compile(
            r"\bi (?:live|reside) in\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:and|with|while|but|because)\b|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bi moved (?:from\s+[^,.!?;]+\s+)?to\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:last|this)\s+(?:week|month|year)|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:ich\s+)?(?:wohne|lebe) in\s+(?P<value>[^,.!?;]+?)"
            r"(?=\s+(?:und|aber|während|weil)\b|[,.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(r"\bnach\s+(?P<value>[^,.!?;]+?)\s+gezogen\b", re.IGNORECASE),
        re.compile(r"\b(?:vivo|resido) en\s+(?P<value>[^,.!?;]+)", re.IGNORECASE),
        re.compile(r"\bme mudé(?:\s+de\s+[^,.!?;]+)?\s+a\s+(?P<value>[^,.!?;]+)", re.IGNORECASE),
        re.compile(r"\b(?:j['’]habite|je vis) à\s+(?P<value>[^,.!?;]+)", re.IGNORECASE),
        re.compile(r"\bdéménag\w*(?:\s+de\s+[^,.!?;]+)?\s+à\s+(?P<value>[^,.!?;]+)", re.IGNORECASE),
        re.compile(r"\b(?:vivo|abito) (?:a|in)\s+(?P<value>[^,.!?;]+)", re.IGNORECASE),
        re.compile(
            r"\btrasfer\w*(?:\s+da\s+[^,.!?;]+)?\s+ad?\s+(?P<value>[^,.!?;]+)", re.IGNORECASE
        ),
        re.compile(r"\b(?:moro|vivo) em\s+(?P<value>[^,.!?;]+)", re.IGNORECASE),
        re.compile(r"\bmudei(?:\s+de\s+[^,.!?;]+)?\s+para\s+(?P<value>[^,.!?;]+)", re.IGNORECASE),
        re.compile(r"\bя\s+живу\s+в\s+(?P<value>[^,.!?;]+)", re.IGNORECASE),
        re.compile(r"\bпереех\w*(?:\s+из\s+[^,.!?;]+)?\s+в\s+(?P<value>[^,.!?;]+)", re.IGNORECASE),
        re.compile(r"मैं\s+(?P<value>[^।,.!?]+?)\s+में\s+रहत(?:ा|ी)\s+हूँ", re.IGNORECASE),
        re.compile(r"से\s+(?P<value>[^।,.!?]+?)\s+चली\s+गई", re.IGNORECASE),
        re.compile(
            r"(?:^|\s)و?(?:أعيش|اعيش|أسكن|اسكن)\s+في\s+(?P<value>[^،.!؟?؛;]+?)"
            r"(?=\s+و(?:أ|ا|ي|ن|ت)\w*|[،,.!؟?؛;]|$)",
            re.IGNORECASE,
        ),
        re.compile(r"انتقلت(?:\s+من\s+[^،.!؟?؛;]+)?\s+إلى\s+(?P<value>[^،.!؟?؛;]+)", re.IGNORECASE),
        re.compile(r"(?:我)?住在\s*(?P<value>[^，。！？；,.!?;]+)"),
        re.compile(r"搬到(?:了)?\s*(?P<value>[^，。！？；,.!?;]+)"),
        re.compile(r"(?:私は)?\s*(?P<value>[^、。！？,.!?;]+?)\s*に住んでいます"),
        re.compile(
            r"(?:[^、。！？,.!?;]+から)?(?P<value>[^、。！？,.!?;]+?)(?:へ|に)引っ越しました"
        ),
    ),
}


@dataclass(frozen=True)
class ApplyResult:
    status: str
    changed_memory_ids: list[str]
    rejected_candidates: int


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def clean_text(value: str, max_chars: int) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = "".join(character for character in value if character.isprintable())
    value = value.replace("<", "‹").replace(">", "›")
    return " ".join(value.split())[:max_chars].strip()


def normalize_key(value: str, kind: str, content: str) -> str:
    key = _KEY_RE.sub("_", value.casefold().strip()).strip("_.-")[:80]
    if key:
        return key
    words = [word.casefold() for word in _TOKEN_RE.findall(content)[:5]]
    suffix = "_".join(words) or uuid4().hex[:10]
    return f"{kind}.{suffix}"[:80]


def tokenize(value: str) -> set[str]:
    tokens = {
        _TOKEN_ALIASES.get(token.casefold(), token.casefold())
        for token in _TOKEN_RE.findall(value)
        if len(token) > 1 and token.casefold() not in {"the", "and", "that", "with", "this"}
    }
    tokens.update(
        concept for concept, pattern in _CONCEPT_PATTERNS.items() if pattern.search(value)
    )
    return tokens


def grounded_value_occurs(container: str, value: str) -> bool:
    """Check a meaningful value span, without accepting substrings inside words."""

    if len(value) < 2 or sum(character.isalnum() for character in value) < 2:
        return False
    folded_container = container.casefold()
    folded_value = value.casefold()
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", folded_value):
        return folded_value in folded_container
    return bool(re.search(rf"(?<!\w){re.escape(folded_value)}(?!\w)", folded_container, re.UNICODE))


def evidence_supports_key(key: str, evidence: str) -> bool:
    concept = {
        "identity.location": "location",
        "identity.name": "name",
    }.get(key)
    return concept is None or bool(_CONCEPT_PATTERNS[concept].search(evidence))


def normalize_slot_value(key: str, value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip(" \t\n.,!?;:，。！？；؟、")
    if key != "identity.location":
        return " ".join(normalized.split())
    normalized = re.sub(r"^(?:in|at|en|à|a|ad|em|в|في)\s+", "", normalized)
    normalized = re.sub(r"^住在", "", normalized)
    normalized = re.sub(r"に住んでいます$", "", normalized)
    normalized = re.sub(r"\s+में\s+रह.*$", "", normalized)
    normalized = re.sub(
        r"\s+(?:last (?:week|month|year)|le mois dernier|no mês passado|الشهر الماضي)$",
        "",
        normalized,
    )
    return " ".join(normalized.split()).strip(" \t\n.,!?;:，。！？；؟、")


def evidence_value_matches_slot(key: str, value: str, evidence: str) -> bool:
    patterns = _SLOT_VALUE_PATTERNS.get(key)
    if patterns is None:
        return True
    for pattern in patterns:
        match = pattern.search(evidence)
        if not match:
            continue
        extracted = clean_text(match.group("value"), 180)
        if normalize_slot_value(key, value) == normalize_slot_value(key, extracted):
            return True
    return False


class MemoryService:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    @staticmethod
    def is_forget_request(message: str) -> bool:
        return bool(_FORGET_INTENT_RE.search(clean_text(message, 4_000)))

    @staticmethod
    def freshness(memory: dict[str, Any], now: datetime | None = None) -> float:
        policy = LIFECYCLE_POLICIES[memory["stability"]]
        confirmed = parse_datetime(memory["last_confirmed_at"])
        age_days = max(0.0, ((now or utc_now()) - confirmed).total_seconds() / 86_400)
        return math.exp(-math.log(2) * age_days / policy.half_life_days)

    @staticmethod
    def lifecycle_state(memory: dict[str, Any], now: datetime | None = None) -> str:
        return "review" if (now or utc_now()) >= parse_datetime(memory["review_at"]) else "fresh"

    def memory_views(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or utc_now()
        views: list[dict[str, Any]] = []
        for memory in self.database.list_memories():
            view = dict(memory)
            view["key"] = view.pop("memory_key")
            view["freshness"] = round(self.freshness(memory, current), 4)
            view["lifecycle_state"] = self.lifecycle_state(memory, current)
            view.pop("sensitivity", None)
            view.pop("status", None)
            view.pop("source_message_id", None)
            views.append(view)
        return views

    def metrics(self, now: datetime | None = None) -> dict[str, Any]:
        memories = self.database.list_memories()
        freshness_values = [self.freshness(memory, now) for memory in memories]
        usage = self.database.usage_metrics()
        return {
            "active_memories": len(memories),
            "max_memories": self.settings.memory_max_facts,
            "review_memories": sum(
                self.lifecycle_state(memory, now) == "review" for memory in memories
            ),
            "average_freshness": round(
                sum(freshness_values) / len(freshness_values) if freshness_values else 1.0,
                4,
            ),
            **usage,
        }

    @staticmethod
    def _forget_target_is_unambiguous(
        connection: sqlite3.Connection,
        memory_id: str,
        memory_key: str,
        source_message: str,
    ) -> bool:
        if memory_key == "identity.location" and _LOCATION_DELETE_RE.search(source_message):
            return True
        if memory_key == "identity.name" and _NAME_DELETE_RE.search(source_message):
            return True
        source_tokens = tokenize(source_message)
        rows = connection.execute(
            """
            SELECT id, memory_key, content FROM memories
            WHERE user_id = ? AND status = 'active'
            """,
            (DEMO_USER_ID,),
        ).fetchall()
        scores = [
            (
                len(source_tokens & tokenize(f"{row['memory_key']} {row['content']}")),
                str(row["id"]),
            )
            for row in rows
        ]
        if not scores:
            return False
        target_score = next((score for score, row_id in scores if row_id == memory_id), 0)
        best_score = max(score for score, _ in scores)
        return (
            target_score > 0
            and target_score == best_score
            and sum(score == best_score for score, _ in scores) == 1
        )

    @staticmethod
    def prompt_context(profile: dict[str, Any] | None) -> str:
        """Serialize the complete fact-capped profile; no search or selection occurs."""

        return json.dumps(
            {
                "complete_user_memory": str(profile["content"]) if profile else "",
                "profile_version": int(profile["version"]) if profile else None,
                "updated_at": str(profile["created_at"]) if profile else None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def apply_consolidation(
        self,
        consolidation: MemoryConsolidation,
        *,
        source_message_id: str,
        source_message: str,
        now: datetime | None = None,
    ) -> ApplyResult:
        current = now or utc_now()
        timestamp = iso(current)
        changed_ids: list[str] = []
        rejected = 0
        deleted_any = False

        with self.database.connection() as connection:
            for candidate in consolidation.candidates[:24]:
                accepted, changed_id, deleted = self._apply_candidate(
                    connection,
                    candidate,
                    source_message_id=source_message_id,
                    source_message=source_message,
                    current=current,
                    timestamp=timestamp,
                )
                if not accepted:
                    rejected += 1
                if changed_id:
                    changed_ids.append(changed_id)
                deleted_any = deleted_any or deleted

            if deleted_any:
                # Historical prose can contain forgotten payloads. Scrub it rather
                # than pretending a tombstone is erasure.
                connection.execute(
                    "DELETE FROM profile_snapshots WHERE user_id = ?", (DEMO_USER_ID,)
                )

            if changed_ids or deleted_any:
                rows = connection.execute(
                    """
                    SELECT id, content, version FROM memories
                    WHERE user_id = ? AND status = 'active'
                    ORDER BY importance DESC, updated_at DESC
                    """,
                    (DEMO_USER_ID,),
                ).fetchall()
                self._write_profile(
                    connection,
                    self._build_profile([dict(row) for row in rows]),
                    self._profile_sources(rows),
                    source_message_id,
                    current,
                )

        status = "updated" if changed_ids or deleted_any else "unchanged"
        return ApplyResult(
            status=status, changed_memory_ids=changed_ids, rejected_candidates=rejected
        )

    def _apply_candidate(
        self,
        connection: sqlite3.Connection,
        candidate: MemoryCandidate,
        *,
        source_message_id: str,
        source_message: str,
        current: datetime,
        timestamp: str,
    ) -> tuple[bool, str | None, bool]:
        content = clean_text(candidate.content, 320)
        value = clean_text(candidate.value, 180)
        evidence = clean_text(candidate.evidence, 180)
        cleaned_source = clean_text(source_message, 4_000)
        key = normalize_key(candidate.key, candidate.kind, content)
        # The model proposes changes, but the server only accepts candidates
        # carrying a literal excerpt from the current user message. This makes
        # provenance enforceable rather than a prompt-only promise.
        if (
            not evidence
            or evidence.casefold() not in cleaned_source.casefold()
            or _SECRET_RE.search(evidence)
            or _INSTRUCTION_RE.search(evidence)
        ):
            return False, None, False
        if candidate.action in {"create", "update"}:
            if (
                not content
                or not value
                or not grounded_value_occurs(evidence, value)
                or not grounded_value_occurs(content, value)
                or not evidence_supports_key(key, evidence)
                or not evidence_value_matches_slot(key, value, evidence)
                or candidate.confidence < _MIN_MODEL_CONFIDENCE
                or _NON_SELF_CONTEXT_RE.search(cleaned_source)
                or _SECRET_RE.search(content)
                or _INSTRUCTION_RE.search(content)
                or (
                    (_SENSITIVE_KEY_RE.search(key) or _SENSITIVE_CONTENT_RE.search(content))
                    and not self.settings.memory_allow_sensitive
                )
                or candidate.sensitivity == "secret"
                or (
                    candidate.sensitivity == "sensitive"
                    and not self.settings.memory_allow_sensitive
                )
            ):
                return False, None, False
        prefix = key.partition(".")[0]
        if prefix not in _KIND_PREFIXES[candidate.kind]:
            return False, None, False
        if candidate.action in {"update", "confirm", "forget"} and not candidate.target_memory_id:
            return False, None, False

        existing = None
        if candidate.target_memory_id:
            existing = connection.execute(
                "SELECT * FROM memories WHERE id = ? AND user_id = ? AND status = 'active'",
                (candidate.target_memory_id, DEMO_USER_ID),
            ).fetchone()
            if existing is None:
                return False, None, False
        elif key:
            existing = connection.execute(
                "SELECT * FROM memories WHERE memory_key = ? AND user_id = ? AND status = 'active'",
                (key, DEMO_USER_ID),
            ).fetchone()
        if existing is not None and candidate.target_memory_id:
            if (
                key != str(existing["memory_key"])
                or candidate.kind != str(existing["kind"])
                or candidate.stability != str(existing["stability"])
            ):
                return False, None, False
        if candidate.action == "create" and existing is not None:
            return False, None, False

        if candidate.action == "forget":
            if (
                existing is None
                or not _FORGET_INTENT_RE.search(cleaned_source)
                or not self._forget_target_is_unambiguous(
                    connection,
                    str(existing["id"]),
                    str(existing["memory_key"]),
                    cleaned_source,
                )
            ):
                return False, None, False
            memory_id = str(existing["id"])
            kind = str(existing["kind"])
            connection.execute(
                "DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, DEMO_USER_ID)
            )
            self.database.add_event(
                "forgotten",
                memory_id=memory_id,
                kind=kind,
                created_at=current,
                connection=connection,
            )
            return True, memory_id, True

        if candidate.action == "confirm":
            if existing is None or not _CONFIRM_INTENT_RE.search(cleaned_source):
                return False, None, False
            policy = LIFECYCLE_POLICIES[str(existing["stability"])]
            version = int(existing["version"]) + 1
            connection.execute(
                """
                UPDATE memories
                SET confidence = ?, version = ?, updated_at = ?, last_confirmed_at = ?,
                    review_at = ?, expires_at = ?, source_message_id = ?, source_excerpt = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    max(float(existing["confidence"]), float(candidate.confidence)),
                    version,
                    timestamp,
                    timestamp,
                    iso(current + timedelta(days=policy.review_after_days)),
                    iso(current + timedelta(days=policy.expire_after_days)),
                    source_message_id,
                    evidence,
                    existing["id"],
                    DEMO_USER_ID,
                ),
            )
            self._write_version(
                connection,
                str(existing["id"]),
                version,
                "confirmed",
                str(existing["content"]),
                source_message_id,
                current,
            )
            self.database.add_event(
                "confirmed",
                memory_id=str(existing["id"]),
                kind=str(existing["kind"]),
                created_at=current,
                connection=connection,
            )
            return True, str(existing["id"]), False

        storage_kind = str(existing["kind"]) if existing is not None else candidate.kind
        storage_stability = (
            str(existing["stability"]) if existing is not None else candidate.stability
        )
        storage_importance = (
            int(existing["importance"]) if existing is not None else candidate.importance
        )
        sensitivity_rank = {"normal": 0, "sensitive": 1, "secret": 2}
        storage_sensitivity = candidate.sensitivity
        if (
            existing is not None
            and sensitivity_rank[str(existing["sensitivity"])]
            > sensitivity_rank[storage_sensitivity]
        ):
            storage_sensitivity = str(existing["sensitivity"])
        policy = LIFECYCLE_POLICIES[storage_stability]
        review_at = iso(current + timedelta(days=policy.review_after_days))
        expires_at = iso(current + timedelta(days=policy.expire_after_days))
        if existing is None and not self._has_memory_capacity(connection):
            return False, None, False

        if existing is None:
            if candidate.action == "update":
                return False, None, False
            memory_id = f"mem_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO memories(
                    id, user_id, memory_key, content, kind, stability, importance,
                    confidence, sensitivity, status, version, created_at, updated_at,
                    last_confirmed_at, review_at, expires_at, source_message_id,
                    source_excerpt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    DEMO_USER_ID,
                    key,
                    content,
                    storage_kind,
                    storage_stability,
                    storage_importance,
                    candidate.confidence,
                    storage_sensitivity,
                    timestamp,
                    timestamp,
                    timestamp,
                    review_at,
                    expires_at,
                    source_message_id,
                    evidence,
                ),
            )
            self._write_version(
                connection, memory_id, 1, "created", content, source_message_id, current
            )
            self.database.add_event(
                "created",
                memory_id=memory_id,
                kind=storage_kind,
                created_at=current,
                connection=connection,
            )
            return True, memory_id, False

        memory_id = str(existing["id"])
        same_content = clean_text(str(existing["content"]), 320).casefold() == content.casefold()
        version = int(existing["version"]) + 1
        action = "confirmed" if same_content else "updated"
        connection.execute(
            """
            UPDATE memories
            SET memory_key = ?, content = ?, kind = ?, stability = ?, importance = ?,
                confidence = ?, sensitivity = ?, version = ?, updated_at = ?,
                last_confirmed_at = ?, review_at = ?, expires_at = ?,
                source_message_id = ?, source_excerpt = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                key,
                content,
                storage_kind,
                storage_stability,
                storage_importance,
                max(float(existing["confidence"]), candidate.confidence)
                if same_content
                else candidate.confidence,
                storage_sensitivity,
                version,
                timestamp,
                timestamp,
                review_at,
                expires_at,
                source_message_id,
                evidence,
                memory_id,
                DEMO_USER_ID,
            ),
        )
        self._write_version(
            connection, memory_id, version, action, content, source_message_id, current
        )
        self.database.add_event(
            action,
            memory_id=memory_id,
            kind=storage_kind,
            created_at=current,
            connection=connection,
        )
        return True, memory_id, False

    @staticmethod
    def _write_version(
        connection: sqlite3.Connection,
        memory_id: str,
        version: int,
        action: str,
        content: str,
        source_message_id: str | None,
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_versions(
                memory_id, user_id, version, action, content, source_message_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, DEMO_USER_ID, version, action, content, source_message_id, iso(created_at)),
        )

    def _has_memory_capacity(self, connection: sqlite3.Connection) -> bool:
        """Enforce the configured hard ceiling for this user's active facts."""

        row = connection.execute(
            """
            SELECT COUNT(*) AS count FROM memories
            WHERE user_id = ? AND status = 'active'
            """,
            (DEMO_USER_ID,),
        ).fetchone()
        return int(row["count"]) < self.settings.memory_max_facts

    def _write_profile(
        self,
        connection: sqlite3.Connection,
        content: str,
        memory_sources: list[dict[str, Any]],
        source_message_id: str | None,
        created_at: datetime,
    ) -> None:
        serialized_sources = json.dumps(memory_sources, separators=(",", ":"))
        previous = connection.execute(
            """
            SELECT version, content, derived_from_memory_ids FROM profile_snapshots
            WHERE user_id = ? ORDER BY version DESC LIMIT 1
            """,
            (DEMO_USER_ID,),
        ).fetchone()
        if (
            previous
            and str(previous["content"]) == content
            and str(previous["derived_from_memory_ids"]) == serialized_sources
        ):
            return
        if previous:
            version = int(previous["version"]) + 1
        else:
            profile_events = connection.execute(
                """
                SELECT COUNT(*) AS count FROM memory_events
                WHERE user_id = ? AND action = 'profile'
                """,
                (DEMO_USER_ID,),
            ).fetchone()
            version = int(profile_events["count"]) + 1
        connection.execute(
            """
            INSERT INTO profile_snapshots(
                id, user_id, version, content, derived_from_memory_ids,
                source_message_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"profile_{uuid4().hex}",
                DEMO_USER_ID,
                version,
                # Each fact is normalized to at most 320 characters. Account for
                # terminal punctuation and separators without truncating any fact.
                clean_text(content, self.settings.memory_max_facts * 322),
                serialized_sources,
                source_message_id,
                iso(created_at),
            ),
        )
        self.database.add_event("profile", created_at=created_at, connection=connection)

    @staticmethod
    def _profile_sources(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [{"memory_id": str(row["id"]), "version": int(row["version"])} for row in rows]

    @staticmethod
    def _build_profile(memories: list[dict[str, Any]]) -> str:
        facts = [clean_text(str(memory["content"]), 320) for memory in memories]
        sentences = [
            fact if fact.endswith((".", "!", "?", "。", "！", "？", "؟")) else f"{fact}."
            for fact in facts
            if fact
        ]
        return " ".join(sentence for sentence in sentences if sentence)

    def edit_memory(
        self,
        memory_id: str,
        content: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = now or utc_now()
        cleaned = clean_text(content, 320)
        if (
            not cleaned
            or _SECRET_RE.search(cleaned)
            or _INSTRUCTION_RE.search(cleaned)
            or (_SENSITIVE_CONTENT_RE.search(cleaned) and not self.settings.memory_allow_sensitive)
        ):
            return False
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ? AND user_id = ? AND status = 'active'",
                (memory_id, DEMO_USER_ID),
            ).fetchone()
            if not row:
                return False
            if (
                str(row["memory_key"]) == "identity.location"
                and any(character.isdigit() for character in cleaned)
                and not self.settings.memory_allow_sensitive
            ):
                return False
            policy = LIFECYCLE_POLICIES[str(row["stability"])]
            version = int(row["version"]) + 1
            sensitivity = str(row["sensitivity"])
            if _SENSITIVE_CONTENT_RE.search(cleaned):
                sensitivity = "sensitive"
            connection.execute(
                """
                UPDATE memories SET content = ?, confidence = 1.0, version = ?, updated_at = ?,
                    last_confirmed_at = ?, review_at = ?, expires_at = ?, source_excerpt = ?,
                    source_message_id = NULL, sensitivity = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    cleaned,
                    version,
                    iso(current),
                    iso(current),
                    iso(current + timedelta(days=policy.review_after_days)),
                    iso(current + timedelta(days=policy.expire_after_days)),
                    cleaned[:180],
                    sensitivity,
                    memory_id,
                    DEMO_USER_ID,
                ),
            )
            self._write_version(connection, memory_id, version, "updated", cleaned, None, current)
            self.database.add_event(
                "updated",
                memory_id=memory_id,
                kind=str(row["kind"]),
                created_at=current,
                connection=connection,
            )
            rows = connection.execute(
                "SELECT id, content, version FROM memories WHERE user_id = ? ORDER BY importance DESC, updated_at DESC",
                (DEMO_USER_ID,),
            ).fetchall()
            self._write_profile(
                connection,
                self._build_profile([dict(item) for item in rows]),
                self._profile_sources(rows),
                None,
                current,
            )
        return True

    def confirm_memory(self, memory_id: str, now: datetime | None = None) -> bool:
        current = now or utc_now()
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ? AND user_id = ? AND status = 'active'",
                (memory_id, DEMO_USER_ID),
            ).fetchone()
            if not row:
                return False
            policy = LIFECYCLE_POLICIES[str(row["stability"])]
            version = int(row["version"]) + 1
            connection.execute(
                """
                UPDATE memories SET confidence = 1.0, version = ?, updated_at = ?,
                    last_confirmed_at = ?, review_at = ?, expires_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    version,
                    iso(current),
                    iso(current),
                    iso(current + timedelta(days=policy.review_after_days)),
                    iso(current + timedelta(days=policy.expire_after_days)),
                    memory_id,
                    DEMO_USER_ID,
                ),
            )
            self._write_version(
                connection, memory_id, version, "confirmed", str(row["content"]), None, current
            )
            self.database.add_event(
                "confirmed",
                memory_id=memory_id,
                kind=str(row["kind"]),
                created_at=current,
                connection=connection,
            )
            rows = connection.execute(
                "SELECT id, content, version FROM memories WHERE user_id = ? ORDER BY importance DESC, updated_at DESC",
                (DEMO_USER_ID,),
            ).fetchall()
            self._write_profile(
                connection,
                self._build_profile([dict(item) for item in rows]),
                self._profile_sources(rows),
                None,
                current,
            )
        return True

    def forget_memory(self, memory_id: str, now: datetime | None = None) -> bool:
        current = now or utc_now()
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT kind FROM memories WHERE id = ? AND user_id = ?",
                (memory_id, DEMO_USER_ID),
            ).fetchone()
            if not row:
                return False
            connection.execute(
                "DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, DEMO_USER_ID)
            )
            connection.execute("DELETE FROM profile_snapshots WHERE user_id = ?", (DEMO_USER_ID,))
            self.database.add_event(
                "forgotten",
                memory_id=memory_id,
                kind=str(row["kind"]),
                created_at=current,
                connection=connection,
            )
            rows = connection.execute(
                "SELECT id, content, version FROM memories WHERE user_id = ? ORDER BY importance DESC, updated_at DESC",
                (DEMO_USER_ID,),
            ).fetchall()
            self._write_profile(
                connection,
                self._build_profile([dict(item) for item in rows]),
                self._profile_sources(rows),
                None,
                current,
            )
        return True

    def sweep(self, *, now: datetime | None = None) -> int:
        current = now or utc_now()
        expired: list[sqlite3.Row] = []
        with self.database.connection() as connection:
            expired = connection.execute(
                """
                SELECT id, kind FROM memories
                WHERE user_id = ? AND expires_at <= ?
                """,
                (DEMO_USER_ID, iso(current)),
            ).fetchall()
            if not expired:
                return 0
            connection.executemany(
                "DELETE FROM memories WHERE id = ? AND user_id = ?",
                [(row["id"], DEMO_USER_ID) for row in expired],
            )
            connection.execute("DELETE FROM profile_snapshots WHERE user_id = ?", (DEMO_USER_ID,))
            for row in expired:
                self.database.add_event(
                    "expired",
                    memory_id=str(row["id"]),
                    kind=str(row["kind"]),
                    created_at=current,
                    connection=connection,
                )
            rows = connection.execute(
                "SELECT id, content, version FROM memories WHERE user_id = ? ORDER BY importance DESC, updated_at DESC",
                (DEMO_USER_ID,),
            ).fetchall()
            self._write_profile(
                connection,
                self._build_profile([dict(item) for item in rows]),
                self._profile_sources(rows),
                None,
                current,
            )
        return len(expired)
