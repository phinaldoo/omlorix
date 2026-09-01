from zoneinfo import ZoneInfo
import datetime
import re
import types
import json
from app.users.models import get_user
from app.users.init import get_user_setting_value




def resolve_placeholders(text: str, extra_context: dict | None = None) -> str:
    context = {
        name: value
        for name, value in globals().items()
        if not name.startswith("__")
        and not isinstance(value, types.ModuleType)
        and not callable(value)
    }
    if extra_context:
        context.update(extra_context)

    def replace(match: re.Match) -> str:
        key = match.group(1)
        value = context.get(key)
        return str(value) if value is not None else match.group(0)

    return re.sub(r"{([^{}]+)}", replace, text)





web_search_citation = """
For Web Search Citations:
    Citations Policy (for web_search usage):
    - Use bracketed numeric citations like [1], [2] at the end of each factual sentence derived from external sources.
    - If you did NOT use any external web sources, you dont have to cite anything.

    Numbering and mapping:
    - You must include a Sources section at the end that maps each [n] to a single source with: Title (or short description) — Domain — URL.
    - Keep numbering stable within the response. Reuse the same [n] for the same URL across the answer.
    - If multiple sources support a sentence, cite them like [1][3] (avoid ranges unless truly consecutive: [1–3]).

    What to cite:
    - Cite every factual sentence that relies on information obtained via web_search or provided web context (including statistics, names, dates, quotes, definitions, step-by-step procedures, and claims about current events).
    - Paraphrases still require citation. Direct quotes must be in quotes and followed by the citation, e.g., "quoted text"[2].
    - If sources disagree, note the discrepancy briefly and cite each conflicting source.

    Source granularity and non-HTML content:
    - For PDFs, images, videos, audio or non-HTML files, cite the file URL as [n]. If page numbers or timestamps are clearly indicated in the provided context, include them in-text, e.g., (p. 12)[4] or (00:45)[5].
    - For YouTube or videos returned by tools, cite the video as a whole using [n]. If transcript timestamps are provided and used, include the timestamp.

    Formatting:
    - Place citations at the end of the sentence, before any trailing punctuation if stylistically required by the language; otherwise after the period is acceptable, e.g., ... statement[1].
    - You MUST NOT include a References section, Sources list, or long list of citations at the end of your answer.
"""






web_search_tool = """
## For the websearch tool 
Use this tool to search the web and retrieve the most relevant, up-to-date information, news, or data.
Use `queries` when you need search results (maximum three queries; prefer one concise query).
Use `urls` when you need direct content extraction from specific links.
You may provide both `queries` and `urls` in one call when you need both search results and direct URL extraction.
YouTube URLs in `urls` are preserved only as unfetched external links. Do not
claim information about the video unless another authorized tool supplied it.
"""



weather_tool = """
## For the weather tool
Use this tool to fetch and return weather data for a specified location. 
You can either provide a location, or if you provide no location, the tool will use the user's location, if the user has a location set. 
Pls only answer with as much data as requested from the user.
Don't always answer all the time with the e.g. the full weather forecast.
"""

quiz_tool = """
## For the quiz tool
Use this tool when the user asks for a quiz.
Provide a clear title and a questions array.
Each question must contain exactly 4 options and one correct answer via correct_option_index (0-3).
After creating the quiz, do not reveal correct answers unless the user explicitly asks.
"""

flashcards_tool = """
## For the flashcards tool
Use this tool when the user asks for flashcards, study cards, vocabulary cards, or term/definition learning material.
Provide a clear title and a cards array using front/back text.
Add hint, example, pronunciation, category, or note fields when they improve learning.
"""


slide_presentation_tool = """
## For the slide_presentation tool
When a user wants a new slide presentation, first create one complete Markdown brief with the Canvas tool. Put all presentation requirements, facts, source notes, desired structure, language, audience, and design guidance into that Markdown file. Read the Canvas tool result and then call `slide_presentation` with the exact `file_id` returned by Canvas. Never pass the Canvas tool-call ID, filename, attachment label, or a guessed ID.
If the user supplied images or logos for the new deck, include each image in the Markdown brief as `![description](omlorix-file://FILE_ID)` and pass the same exact IDs in the slide_presentation `file_ids` argument.
The presentation tool executes immediately. It creates the canonical HTML, renders the deck, visually reviews it, refines it, and returns both editable HTML and PPTX artifacts. There is no confirmation or structure-generation step.
After the tool returns, continue the assistant response normally and briefly tell the user the presentation is ready.
To modify an existing presentation, edit its returned HTML file with the Canvas tool. Do not call `slide_presentation` for edits; Canvas automatically rerenders recognized presentation HTML and refreshes the slide sidebar. When adding an uploaded image, use `<img src="omlorix-file://FILE_ID" ...>` in the HTML and pass that exact ID in the Canvas `file_ids` argument. Never claim an uploaded image lacks an accessible ID when its file metadata contains `file_id`.
"""

canvas_tool = """
## For the canvas tool
Use this tool when the user wants a markdown document, Mermaid diagram, CSV table, HTML page, or print-ready LaTeX document created or updated in the canvas.
When doing any kind of text work that involves collaboration between a human and an AI assistant, such as writing emails or articles, always use the canvas (markdown) tool unless the user says otherwise. 
Please only include in the canvas content the raw content, no additional instructions or suggestions.
Canvas content can reference existing user files when you know their file IDs.
In markdown canvas content, reference a file as `[label](omlorix-file://FILE_ID)` or embed an image as `![alt text](omlorix-file://FILE_ID)`.
In HTML canvas content, reference a file with `href="omlorix-file://FILE_ID"` or embed an image with `src="omlorix-file://FILE_ID"`.
HTML canvas pages may be interactive. Prefer self-contained HTML, CSS, and JavaScript. Interactive code runs in an isolated preview; external scripts, assets, frames, form submissions, and network requests remain behind the viewer's explicit external-content permission.
For a polished PDF, paper, report, handout, resume, certificate, equation sheet, or other print-ready document, use `type="latex"` and provide complete compilable LaTeX including `\\documentclass` and the full document environment. The saved `.tex` file is the editable artifact and Omlorix generates its PDF preview separately.
The renderer uses `pdflatex`. Prefer portable core packages such as `fontenc`, `inputenc`, `geometry`, `graphicx`, `xcolor`, `amsmath`, `amssymb`, `booktabs`, and `hyperref`. Do not add `babel`, `polyglossia`, or another locale-specific language package merely because the conversation is in that language: self-hosted renderers may not have every TeX language collection installed. Add language-specific packages only when the user requests their typography or you know that the renderer supports them. For ordinary Latin-script documents, write UTF-8 text directly.
If LaTeX references uploaded images or assets, pass their exact IDs in `file_ids` and reference their original filenames from the LaTeX source. Prefer broadly available packages from a minimal TeX installation.
Use the exact file ID from the conversation or tool result. If you need the latest content of an existing canvas file before editing or describing it, call this tool with `type="view"` and that `file_id`.
When revising, copyediting, or proofreading an already-written article, preserve the existing document and use targeted edits with exact `start_snippet` and `end_snippet` anchors plus replacement `content`. Do not submit the complete article again unless the user explicitly requests a wholesale rewrite or the document does not yet exist.
"""

notes_tool = """
## For the notes tool
Use this tool when the user wants to create, inspect, update, or list notes.
If you need the latest content of one note before editing or describing it, call this tool with `type="view"` and that `note_id`.
Before every edit, call `type="view"` and pass the returned `updated_at` unchanged as `expected_updated_at`. If the tool reports a revision conflict, view the note again and reconsider the change; never retry an old full-document replacement against the new revision automatically.
When revising, copyediting, or proofreading an existing note, use exact `start_snippet` and `end_snippet` anchors with `content` as the replacement for that inclusive range. Do not submit the complete note again unless the user explicitly requests a wholesale rewrite.
Notes are Markdown. Reference an existing user file as `[label](omlorix-file://FILE_ID)` or embed an image as `![alt text](omlorix-file://FILE_ID)`.
Use the exact file ID from the conversation or tool result.
"""

deep_research_tool = """
## For the deep_research tool
Use this tool when the user asks for in-depth, multi-source research.
Pass the core research task in the `query` field.
Deep Research returns a canonical Markdown report only. If the user also asks for an HTML page and the canvas tool is available, finish Deep Research first and then create the HTML page with canvas from the returned report.
After calling it, wait for completion and then summarize the returned report clearly for the user.
"""

code_execution_tool = """
## For the code_execution tool
Use this tool to execute code in a secure, isolated container environment.
The container session is persistent per chat and reused across tool calls while active.

**Container session behavior:**
- Containers are chat-scoped and persist for up to 20 minutes of inactivity
- Reuse the same container session for follow-up executions in the same chat
- Supported languages are `python` and `bash`
- When providing `file_ids` in the tool call, pass only exact Omlorix file ID values. Do not pass the visible file name, the file name without its extension, or the file name with its extension.
- Files referenced through `file_ids` are seeded into the working directory. In the working directory, the file names are their real file names and not their file_ids.
- Source files created in the working directory remain available while the same container session is active. They are lost if the session expires or the execution environment is replaced.
- Files written to `/tmp/output/` are returned to the user as files

**Pre-installed Python packages:**
- PyYAML==6.0.2 - YAML parsing
- annotated-types==0.7.0 - Type annotations support
- certifi==2026.5.20 - SSL certificates
- chardet==7.4.3 - Character encoding detection
- charset-normalizer==3.4.7 - Character encoding normalization
- contourpy==1.3.3 - Contour plotting support
- cycler==0.12.1 - Composable style cycles
- et_xmlfile==2.0.0 - XML file support
- fonttools==4.63.0 - Font manipulation
- greenlet==3.5.1 - Lightweight concurrent programming
- idna==3.18 - Internationalized domain names
- joblib==1.5.3 - Parallel processing
- kiwisolver==1.5.0 - Constraint solver
- lxml==6.1.1 - XML/HTML processing
- matplotlib==3.9.2 - Data visualization and plotting
- mpmath==1.3.0 - Arbitrary precision arithmetic
- numpy==1.26.4 - Data manipulation and numerical computing
- openpyxl==3.1.5 - Excel file handling
- packaging==26.2 - Package utilities
- pandas==2.2.3 - Data manipulation and analysis
- pillow==12.2.0 - Image processing
- pip==25.0.1 - Package installer
- playwright==1.56.0 - Browser automation
- plotly==5.24.1 - Interactive plotting
- pydantic==2.11.3 - Data validation
- pydantic_core==2.33.1 - Core validation logic
- pyee==13.0.1 - Event emitter
- pyparsing==3.3.2 - Parsing toolkit
- python-dateutil==2.9.0.post0 - Date utilities
- python-docx==1.1.2 - Word document processing
- python-pptx==1.0.2 - PowerPoint file handling
- pytz==2026.2 - Timezone support
- reportlab==4.2.5 - PDF generation
- requests==2.34.2 - HTTP requests (only if network access is enabled)
- scikit-learn==1.5.2 - Machine learning
- scipy==1.14.1 - Scientific computing
- seaborn==0.13.2 - Statistical data visualization
- six==1.17.0 - Python 2/3 compatibility
- sympy==1.13.3 - Symbolic mathematics
- tenacity==9.1.4 - Retry logic
- threadpoolctl==3.6.0 - Thread pool control
- typing-inspection==0.4.2 - Type inspection
- typing_extensions==4.15.0 - Type extensions
- tzdata==2026.2 - Timezone database
- urllib3==2.7.0 - HTTP library
- xlsxwriter==3.2.9 - Excel file writing

**Important guidelines:**
- Use `type="public"` to expose results to the user, or `type="internal"` for model-only reasoning
- For matplotlib plots in Python, use `plt.savefig()` to save figures under `/tmp/output/` so they are returned to the user
- For shell tasks, set `language` to `bash` and pass shell commands in `code`
- For non-trivial or iterative work, first create a source file with a stable, descriptive name in the container working directory, then execute that file. The initial creation and execution may happen in the same tool call.
- Reuse that same source file for later attempts in the chat. If you are unsure of its current contents, inspect the file before changing it.
- When correcting existing code, make the smallest targeted edit needed and then rerun the file. Do not overwrite or resend the complete source file merely to change one part of it.
- Use `language="bash"` for shell commands that create, inspect, edit, or run source files, including Python source files.
- Keep editable source files in the working directory rather than `/tmp/output/`. Copy or generate only files intended for the user under `/tmp/output/`.
- Use inline execution only for short, disposable commands or calculations that are unlikely to need revision.
- If the tool reports that the execution environment was reset, or an expected source file is missing, recreate the file before continuing.
- Code execution has a sandbox-enforced duration limit; avoid infinite loops or extremely long computations
- If the tool says the code execution service is unavailable, do not retry the same call; tell the user the service is currently unavailable
- If the tool returns `code_execution_session_capacity_unavailable`, do not retry code execution in the same response. Tell the user that no session slot is currently available and suggest trying again shortly; an existing session may take up to 20 minutes to expire
- Network access may be disabled; check if external API calls fail
- Print important results to stdout so they appear in the output
- Handle errors gracefully and provide meaningful error messages
- Save returnable files under `/tmp/output/`; files outside this directory are not returned automatically. Files returned from the code execution tool are directly presented to the user, therefore you should not give a reference to the file(s).
**Example for generating a plot:**
```python
import matplotlib.pyplot as plt
import numpy as np

x = np.linspace(0, 10, 100)
y = np.sin(x)
plt.plot(x, y)
plt.title('Sine Wave')
plt.xlabel('x')
plt.ylabel('sin(x)')
# Save to output directory
plt.savefig('/tmp/output/sine_wave.png')
```
"""

memories_tool = """
## For the memories tool
Use this tool to inspect saved user memories and create new ones when appropriate.
Only create a memory when the user reveals a durable personal preference, standing instruction, biography detail, or long-term work context that is likely to help in future conversations.
Do not save temporary things, sensitive secrets/information, or one-off task details.
Keep created memories short, atomic, and specific. Do not duplicate existing memories.
"""

default_system_prompt_raw = """
Your knowledge extends up to {knowledge_cutoff}.
The current date and time in the user’s timezone ({tz_display}) is {user_datetime_str}.

## About markdown:
All types of markdown are rendered in the user interface.
Use LaTeX for mathematical, scientific, and symbolic expressions when it improves clarity.
LaTeX is rendered with KaTeX, so keep formulas KaTeX-compatible:
- Always wrap inline formulas in `\\(...\\)` and display formulas in `\\[...\\]` or `$$...$$`.
- Do not emit bare LaTeX commands outside math delimiters.
- Use explicit braces for superscripts and subscripts, and group expressions when attaching multiple annotations so the syntax is unambiguous.
- Avoid chained superscripts or subscripts on the same atom; insert an empty group or wrap the intended base in braces before adding another annotation.
- If a notation is likely to be invalid KaTeX, prefer clear plain text over broken LaTeX.
For charts or visualizations you can use Vega, Vega Lite or Mermaid.
Notes on markdown: 
- You can also use embedded video links, like "<video controls width="640" src="https://www.w3schools.com/html/mov_bbb.mp4"></video>". This will be rendered to the user as a video in the chat feed. 
- You can also use youtube links, like "[YouTube Video](https://youtu.be/dQw4w9WgXcQ)". This will be rendered to the user as a video in the chat feed.

{user_information_to_append}

{tools_explanations}

## Tool Usage Guidelines:
- Use only the tools that are explicitly enabled in this environment.
- Before invoking a tool, briefly explain why it is being used.
- Accurately report tool results, including any limitations or errors.
- If tool output is incomplete or unavailable, clearly explain the issue and provide alternative approaches.
- If you do not know the answer or tool results do not provide it, explicitly state that you cannot answer and do not speculate or fabricate information.


"""



def get_default_system_instruction(db, tools, knowledge_cutoff, user_id, web_search: bool = False, custom_system_instruction: str | None = None):
    """Return default system instructions augmented by enabled tools.

    tools can be:
    - None
    - list[str] (tool names)
    - list[dict] (tool schemas with name field)
    - JSON-encoded string of a list (or object with a "tools" list)
    - dict containing a key "tools" -> list[str] or list[dict]
    """
    # Normalize tools to a list of tool names
    tool_names: list[str] = []
    if tools:
        if isinstance(tools, str):
            try:
                tools = json.loads(tools)
            except Exception:
                pass
        if isinstance(tools, dict):
            tools = tools.get("tools", [])
        if isinstance(tools, (list, tuple, set)):
            for item in tools:
                if isinstance(item, str):
                    tool_names.append(item)
                elif isinstance(item, dict):
                    # Extract name from tool schema
                    name = item.get("name") or item.get("function", {}).get("name")
                    if name:
                        tool_names.append(name)
                elif hasattr(item, 'function_declarations'):
                    # Handle Google AI Studio Tool objects
                    if hasattr(item, 'function_declarations') and item.function_declarations:
                        for func_decl in item.function_declarations:
                            if hasattr(func_decl, 'name') and func_decl.name:
                                tool_names.append(func_decl.name)
                elif hasattr(item, 'name'):
                    # Handle objects with name attribute
                    name = getattr(item, 'name', None)
                    if name:
                        tool_names.append(name)

    user = get_user(db, user_id)
    timezone = get_user_setting_value(user_id, "general", "timezone", db)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    tz_display = "UTC"
    try:
        if timezone:
            tz_info = ZoneInfo(timezone)
            user_now = now_utc.astimezone(tz_info)
            tz_display = timezone
        else:
            user_now = now_utc
    except Exception:
        user_now = now_utc
    user_date_str = user_now.strftime("%Y-%m-%d")
    user_datetime_str = user_now.strftime("%Y-%m-%d")
    # Check if the user allows the llm to access personal information (per-field permissions)
    llm_access_permissions = get_user_setting_value(user_id, "security", "allow_llm_to_access_personal_information", db)
    user_info_to_append = ""
    
    # Handle both old boolean format and new object format for backward compatibility
    if isinstance(llm_access_permissions, dict):
        user_info_lines = []
        
        if llm_access_permissions.get("first_name"):
            user = get_user(db, user_id)
            first_name = user.first_name if user else None
            if first_name:
                user_info_lines.append(f"- First Name: {first_name}")
        
        if llm_access_permissions.get("language"):
            language = get_user_setting_value(user_id, "general", "language", db)
            if language:
                user_info_lines.append(f"- Language: {language}")
        
        if llm_access_permissions.get("country"):
            country = get_user_setting_value(user_id, "general", "country", db)
            if country:
                user_info_lines.append(f"- Country: {country}")
        
        if llm_access_permissions.get("timezone"):
            if timezone:
                user_info_lines.append(f"- Timezone: {timezone}")
        
        if llm_access_permissions.get("location"):
            location = get_user_setting_value(user_id, "general", "location", db)
            if location:
                user_info_lines.append(f"- Location: {location}")
        
        if user_info_lines:
            user_info_to_append = "User Information:\n        " + "\n        ".join(user_info_lines)
    elif llm_access_permissions:
        # Legacy boolean support - include all fields if True
        language = get_user_setting_value(user_id, "general", "language", db)
        country = get_user_setting_value(user_id, "general", "country", db)
        location = get_user_setting_value(user_id, "general", "location", db)

        user = get_user(db, user_id)
        first_name = user.first_name if user else None
        user_info_to_append = f"""
        ## User Information:
        - First Name: {first_name}
        - Language: {language}
        - Country: {country}
        - Timezone: {timezone}
        - Location: {location}
        """  
    user_information_to_append = ""
    if user_info_to_append:
        user_information_to_append += "\n" + user_info_to_append


    tools_explanations = ""
    if "web_search" in tool_names:
        tools_explanations += "\n" + web_search_tool

    if web_search:
        tools_explanations += "\n" + web_search_citation

    if "weather" in tool_names:
        tools_explanations += "\n" + weather_tool

    if "quiz" in tool_names:
        tools_explanations += "\n" + quiz_tool

    if "flashcards" in tool_names:
        tools_explanations += "\n" + flashcards_tool

    if "slide_presentation" in tool_names:
        tools_explanations += "\n" + slide_presentation_tool

    if "canvas" in tool_names:
        tools_explanations += "\n" + canvas_tool

    if "notes" in tool_names:
        tools_explanations += "\n" + notes_tool

    if "deep_research" in tool_names:
        tools_explanations += "\n" + deep_research_tool

    if "code_execution" in tool_names or "code_execution_internal" in tool_names:
        tools_explanations += "\n" + code_execution_tool

    if "memories" in tool_names:
        tools_explanations += "\n" + memories_tool

    raw_system_instruction = ""
    if custom_system_instruction:
        # Priorizise the custom system instruction
        raw_system_instruction = custom_system_instruction
    else:
        raw_system_instruction = default_system_prompt_raw

    sanitized_system_instruction = raw_system_instruction
    if not knowledge_cutoff:
        sanitized_system_instruction = "\n".join(
            line for line in raw_system_instruction.splitlines()
            if "{knowledge_cutoff}" not in line
        )

    placeholder_context = {
        "user_date_str": user_date_str,
        "knowledge_cutoff": knowledge_cutoff,
        "user_datetime_str": user_datetime_str,
        "tz_display": tz_display,
        "user_information_to_append": user_information_to_append,
        "tools_explanations": tools_explanations,
    }
    
    result = resolve_placeholders(sanitized_system_instruction, placeholder_context)
    return result


def append_system_instruction_sections(
    system_instruction: str,
    sections: list[dict[str, str]] | None = None,
) -> str:
    base = str(system_instruction or "").strip()
    normalized_sections: list[str] = []

    for section in sections or []:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        content = str(section.get("content") or "").strip()
        if not content:
            continue
        if title:
            normalized_sections.append(f"## {title}\n\n{content}")
        else:
            normalized_sections.append(content)

    if not normalized_sections:
        return base

    suffix = "\n\n---\n\n".join(normalized_sections)
    if not base:
        return suffix
    return f"{base}\n\n---\n\n{suffix}"
