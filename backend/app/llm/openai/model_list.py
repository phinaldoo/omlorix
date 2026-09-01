from datetime import datetime

# Verified against OpenAI's model, pricing, and deprecation documentation on
# 2026-08-07. Shutdown IDs are filtered from both the catalog and discovery
# lists below, while deprecated models that remain callable stay represented.
OPENAI_CATALOG_LAST_VERIFIED = "2026-08-07"
OPENAI_MODELS_DOCS_URL = "https://developers.openai.com/api/docs/models/all"
OPENAI_PRICING_DOCS_URL = "https://developers.openai.com/api/docs/pricing"
OPENAI_DEPRECATIONS_DOCS_URL = "https://developers.openai.com/api/docs/deprecations"

OPENAI_SHUT_DOWN_MODEL_IDS = {
    "chatgpt-4o-latest",
    "codex-mini-latest",
    "gpt-4-1106-vision-preview",
    "gpt-4o-mini-search-preview-2025-03-11",
    "gpt-4o-mini-tts-2025-03-20",
    "gpt-4o-search-preview-2025-03-11",
    "gpt-5-chat-latest",
    "gpt-5-codex",
    "gpt-5.1-chat-latest",
    "gpt-5.1-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
    "gpt-5.2-codex",
    "gpt-audio-mini-2025-10-06",
    "gpt-realtime-mini-2025-10-06",
    "o1-mini",
    "o1-mini-2024-09-12",
    "o1-preview",
    "o1-preview-2024-09-12",
}

# OpenAI keeps deprecated-but-callable IDs available until their announced
# shutdown date. Store those dates separately from the already-shut-down set so
# the catalog remains transparent without prematurely hiding usable endpoints.
OPENAI_ANNOUNCED_SHUTDOWN_DATES = {
    "gpt-3.5-turbo-instruct": "2026-09-28",
    "gpt-3.5-turbo-0125": "2026-10-23",
    "gpt-4-0613": "2026-10-23",
    "gpt-4-turbo": "2026-10-23",
    "gpt-4.1-nano": "2026-10-23",
    "gpt-4o-2024-05-13": "2026-10-23",
    "gpt-5.2-chat-latest": "2026-08-10",
    "gpt-5.3-chat-latest": "2026-08-10",
    "o1-2024-12-17": "2026-10-23",
    "o1-pro-2025-03-19": "2026-10-23",
    "o3-mini-2025-01-31": "2026-10-23",
    "o4-mini-2025-04-16": "2026-10-23",
}

OPENAI_MODEL_DICT = {
    "gpt-5.6-sol": {
        "ids": ["gpt-5.6-sol", "gpt-5.6"],
        "name": "GPT-5.6 Sol",
        "description": "Frontier model for complex professional work",
        "supports_tool_search": True,
        "supports_reasoning_mode": True,
        "reasoning_context": ["auto", "current_turn", "all_turns"],
        "prompt_caching": {
            "ttl": ["30m"],
            "cache_write": True,
        },
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high", "xhigh", "max"],
            "default_thinking_effort": "medium",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1050000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2026, 2, 16),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 2.5,
                "cached_input": 0.25,
                "cache_write": 3.125,
                "output": 15.0,
            },
            "standard": {
                "input": 5.0,
                "cached_input": 0.5,
                "cache_write": 6.25,
                "output": 30.0,
            },
            "priority": {
                "input": 10.0,
                "cached_input": 1.0,
                "cache_write": 12.5,
                "output": 60.0,
            },
            "high_context_pricing": {
                "mark": 272000,
                "flex": {
                    "input": 5.0,
                    "cached_input": 0.5,
                    "cache_write": 6.25,
                    "output": 22.5,
                },
                "standard": {
                    "input": 10.0,
                    "cached_input": 1.0,
                    "cache_write": 12.5,
                    "output": 45.0,
                },
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.6-terra": {
        "ids": ["gpt-5.6-terra"],
        "name": "GPT-5.6 Terra",
        "description": "GPT-5.6 model that balances intelligence and cost",
        "supports_tool_search": True,
        "supports_reasoning_mode": True,
        "reasoning_context": ["auto", "current_turn", "all_turns"],
        "prompt_caching": {
            "ttl": ["30m"],
            "cache_write": True,
        },
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high", "xhigh", "max"],
            "default_thinking_effort": "medium",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1050000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2026, 2, 16),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 1.25,
                "cached_input": 0.125,
                "cache_write": 1.5625,
                "output": 7.5,
            },
            "standard": {
                "input": 2.5,
                "cached_input": 0.25,
                "cache_write": 3.125,
                "output": 15.0,
            },
            "priority": {
                "input": 5.0,
                "cached_input": 0.5,
                "cache_write": 6.25,
                "output": 30.0,
            },
            "high_context_pricing": {
                "mark": 272000,
                "flex": {
                    "input": 2.5,
                    "cached_input": 0.25,
                    "cache_write": 3.125,
                    "output": 11.25,
                },
                "standard": {
                    "input": 5.0,
                    "cached_input": 0.5,
                    "cache_write": 6.25,
                    "output": 22.5,
                },
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.6-luna": {
        "ids": ["gpt-5.6-luna"],
        "name": "GPT-5.6 Luna",
        "description": "GPT-5.6 model optimized for cost-sensitive workloads",
        "supports_tool_search": True,
        "supports_reasoning_mode": True,
        "reasoning_context": ["auto", "current_turn", "all_turns"],
        "prompt_caching": {
            "ttl": ["30m"],
            "cache_write": True,
        },
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high", "xhigh", "max"],
            "default_thinking_effort": "medium",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1050000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2026, 2, 16),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 0.1,
                "cached_input": 0.01,
                "cache_write": 0.125,
                "output": 0.6,
            },
            "standard": {
                "input": 0.2,
                "cached_input": 0.02,
                "cache_write": 0.25,
                "output": 1.2,
            },
            "priority": {
                "input": 0.4,
                "cached_input": 0.04,
                "cache_write": 0.5,
                "output": 2.4,
            },
            "high_context_pricing": {
                "mark": 272000,
                "flex": {
                    "input": 0.2,
                    "cached_input": 0.02,
                    "cache_write": 0.25,
                    "output": 0.9,
                },
                "standard": {
                    "input": 0.4,
                    "cached_input": 0.04,
                    "cache_write": 0.5,
                    "output": 1.8,
                },
                "priority": {
                    "input": 0.8,
                    "cached_input": 0.08,
                    "cache_write": 1.0,
                    "output": 3.6,
                },
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.5-pro": {
        "ids": ["gpt-5.5-pro", "gpt-5.5-pro-2026-04-23"],
        "name": "GPT-5.5 Pro",
        "description": "Version of GPT-5.5 that produces smarter and more precise responses.",
        "supports_tool_search": False,
        "thinking": {
            "thinking": True,
            "thinking_effort": ["medium", "high", "xhigh"],
            "default_thinking_effort": "high",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1050000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 12, 1),
        "supported_service_tier": ["flex", "standard"],
        "pricing": {
            "flex": {
                "input": 15.0,
                "output": 90.0,
            },
            "standard": {
                "input": 30.0,
                "output": 180.0,
            },
            "high_context_pricing": {
                "mark": 272000,
                "standard": {
                    "input": 60.0,
                    "output": 270.0,
                },
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.5": {
        "ids": ["gpt-5.5", "gpt-5.5-2026-04-23"],
        "name": "GPT-5.5",
        "description": "A new class of intelligence for coding and professional work.",
        "supports_tool_search": True,
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high", "xhigh"],
            "default_thinking_effort": "medium",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1050000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 12, 1),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 15.0,
            },
            "standard": {
                "input": 5.0,
                "cached_input": 0.5,
                "output": 30.0,
            },
            "priority": {
                "input": 12.5,
                "cached_input": 1.25,
                "output": 75.0,
            },
            "high_context_pricing": {
                "mark": 272000,
                "flex": {
                    "input": 5.0,
                    "cached_input": 0.5,
                    "output": 22.5,
                },
                "standard": {
                    "input": 10.0,
                    "cached_input": 1.0,
                    "output": 45.0,
                },
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.4-mini": {
        "ids": ["gpt-5.4-mini", "gpt-5.4-mini-2026-03-17"],
        "name": "GPT-5.4 mini",
        "description": "Our strongest mini model yet for coding, computer use, and subagents",
        "supports_tool_search": True,
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high", "xhigh"],
            "default_thinking_effort": "none",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 0.375,
                "cached_input": 0.0375,
                "output": 2.25,
            },
            "standard": {
                "input": 0.75,
                "cached_input": 0.075,
                "output": 4.5,
            },
            "priority": {
                "input": 1.5,
                "cached_input": 0.15,
                "output": 9.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.4-nano": {
        "ids": ["gpt-5.4-nano", "gpt-5.4-nano-2026-03-17"],
        "name": "GPT-5.4 nano",
        "description": "Our cheapest GPT-5.4-class model for simple high-volume tasks",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high", "xhigh"],
            "default_thinking_effort": "none",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["flex", "standard"],
        "pricing": {
            "flex": {
                "input": 0.1,
                "cached_input": 0.01,
                "output": 0.625,
            },
            "standard": {
                "input": 0.2,
                "cached_input": 0.02,
                "output": 1.25,
            },
            "native_web_search_tool_call": 0.01,
        },
        "supports_tool_search": False,
    },
    "gpt-5.4-pro": {
        "ids": ["gpt-5.4-pro", "gpt-5.4-pro-2026-03-05"],
        "name": "GPT-5.4 Pro",
        "description": "Version of GPT-5.4 that produces smarter and more precise responses.",
        "supports_tool_search": True,
        "thinking": {
            "thinking": True,
            "thinking_effort": ["medium", "high", "xhigh"],
            "default_thinking_effort": "medium",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1050000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["flex", "standard"],
        "pricing": {
            "flex": {
                "input": 15.0,
                "output": 90.0,
            },
            "standard": {
                "input": 30.0,
                "output": 180.0,
            },
            "high_context_pricing": {
                "mark": 272000,
                "flex": {
                    "input": 30.0,
                    "output": 135.0,
                },
                "standard": {
                    "input": 60.0,
                    "output": 270.0,
                },
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.4": {
        "ids": ["gpt-5.4", "gpt-5.4-2026-03-05"],
        "name": "GPT-5.4",
        "description": "A more affordable model for coding and professional work.",
        "supports_tool_search": True,
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high", "xhigh"],
            "default_thinking_effort": "none",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1050000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 1.25,
                "cached_input": 0.13,
                "output": 7.5,
            },
            "standard": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 15.0,
            },
            "priority": {
                "input": 5.0,
                "cached_input": 0.5,
                "output": 30.0,
            },
            "high_context_pricing": {
                "mark": 272000,
                "flex": {
                    "input": 2.5,
                    "cached_input": 0.25,
                    "output": 11.25,
                },
                "standard": {
                    "input": 5.0,
                    "cached_input": 0.5,
                    "output": 22.5,
                },
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.3-codex": {
        "ids": ["gpt-5.3-codex"],
        "name": "GPT-5.3-Codex",
        "description": "The most capable agentic coding model to date.",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["low", "medium", "high", "xhigh"],
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 1.75,
                "cached_input": 0.175,
                "output": 14.0,
            },
            "priority": {
                "input": 3.5,
                "cached_input": 0.35,
                "output": 28.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.2-codex": {
        "ids": ["gpt-5.2-codex"],
        "name": "GPT-5.2-Codex",
        "description": "Deprecated Our most intelligent coding model optimized for long-horizon, agentic coding tasks.",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["low", "medium", "high", "xhigh"],
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 1.75,
                "cached_input": 0.175,
                "output": 14.0,
            },
            "priority": {
                "input": 3.5,
                "cached_input": 0.35,
                "output": 28.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.2-pro": {
        "ids": ["gpt-5.2-pro", "gpt-5.2-pro-2025-12-11"],
        "name": "GPT-5.2 Pro",
        "description": "Previous pro model for professional work that produces smarter and more precise responses.",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["medium", "high", "xhigh"],
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 21.0,
                "output": 168.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.2-chat": {
        "ids": ["gpt-5.2-chat-latest"],
        "name": "GPT-5.2 Chat",
        "description": "GPT-5.2 model used in ChatGPT",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 16384,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 1.75,
                "cached_input": 0.175,
                "output": 14.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.2": {
        "ids": ["gpt-5.2", "gpt-5.2-2025-12-11"],
        "name": "GPT-5.2",
        "description": "Previous frontier model for professional work with configurable reasoning effort",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high", "xhigh"],
            "default_thinking_effort": "none",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 0.875,
                "cached_input": 0.0875,
                "output": 7.0,
            },
            "standard": {
                "input": 1.75,
                "cached_input": 0.175,
                "output": 14.0,
            },
            "priority": {
                "input": 3.5,
                "cached_input": 0.35,
                "output": 28.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.1": {
        "ids": ["gpt-5.1", "gpt-5.1-2025-11-13"],
        "name": "GPT-5.1",
        "description": "The best model for coding and agentic tasks with configurable reasoning effort",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high"],
            "default_thinking_effort": "none",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2024, 9, 30),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 0.625,
                "cached_input": 0.0625,
                "output": 5.0,
            },
            "standard": {
                "input": 1.25,
                "cached_input": 0.125,
                "output": 10.0,
            },
            "priority": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 20.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.1-codex-max": {
        "ids": ["gpt-5.1-codex-max"],
        "name": "GPT-5.1-Codex-Max",
        "description": "Deprecated A version of GPT-5.1-codex optimized for long running tasks.",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high", "xhigh"],
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2024, 9, 30),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 1.25,
                "cached_input": 0.125,
                "output": 10.0,
            },
            "priority": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 20.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.1-codex": {
        "ids": ["gpt-5.1-codex"],
        "name": "GPT-5.1-Codex",
        "description": "Deprecated A version of GPT-5.1 optimized for agentic coding in Codex.",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2024, 9, 30),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 1.25,
                "cached_input": 0.125,
                "output": 10.0,
            },
            "priority": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 20.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.1-codex-mini": {
        "ids": ["gpt-5.1-codex-mini"],
        "name": "GPT-5.1-Codex mini",
        "description": "Deprecated Smaller, more cost-effective, less-capable version of GPT-5.1-Codex",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["none", "low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": True,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": True,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2024, 9, 30),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 0.25,
                "cached_input": 0.025,
                "output": 2.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5.1-chat": {
        "ids": ["gpt-5.1-chat-latest"],
        "name": "GPT-5.1 Chat",
        "description": "Deprecated GPT-5.1 model used in ChatGPT",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 16384,
        "knowledge_cutoff": datetime(2024, 9, 30),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 1.25,
                "cached_input": 0.125,
                "output": 10.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5-pro": {
        "ids": ["gpt-5-pro", "gpt-5-pro-2025-10-06"],
        "name": "GPT-5 Pro",
        "description": "Version of GPT-5 that produces smarter and more precise responses",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["high"],
            "default_thinking_effort": "high",
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 272000,
        "knowledge_cutoff": datetime(2024, 9, 30),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 15.0,
                "output": 120.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5-chat": {
        "ids": ["gpt-5-chat-latest"],
        "name": "GPT-5 Chat",
        "description": "Deprecated GPT-5 model used in ChatGPT",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 16384,
        "knowledge_cutoff": datetime(2024, 9, 30),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 1.25,
                "cached_input": 0.125,
                "output": 10.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5": {
        "ids": ["gpt-5", "gpt-5-2025-08-07"],
        "name": "GPT-5",
        "description": "Previous intelligent reasoning model for coding and agentic tasks with configurable reasoning effort",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["minimal", "low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2024, 9, 30),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 0.625,
                "cached_input": 0.0625,
                "output": 5.0,
            },
            "standard": {
                "input": 1.25,
                "cached_input": 0.125,
                "output": 10.0,
            },
            "priority": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 20.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5-codex": {
        "ids": ["gpt-5-codex"],
        "name": "GPT-5-Codex",
        "description": "Deprecated A version of GPT-5 optimized for agentic coding in Codex",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["minimal", "low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2024, 9, 30),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 1.25,
                "cached_input": 0.125,
                "output": 10.0,
            },
            "priority": {
                "input": 2.5,
                "cached_input": 0.25,
                "output": 20.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5-mini": {
        "ids": ["gpt-5-mini", "gpt-5-mini-2025-08-07"],
        "name": "GPT-5 mini",
        "description": "Near-frontier intelligence for cost sensitive, low latency, high volume workloads",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["minimal", "low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2024, 5, 31),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 0.125,
                "cached_input": 0.0125,
                "output": 1.0,
            },
            "standard": {
                "input": 0.25,
                "cached_input": 0.025,
                "output": 2.0,
            },
            "priority": {
                "input": 0.45,
                "cached_input": 0.045,
                "output": 3.6,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-5-nano": {
        "ids": ["gpt-5-nano", "gpt-5-nano-2025-08-07"],
        "name": "GPT-5 nano",
        "description": "Fastest, most cost-efficient version of GPT-5",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["minimal", "low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 400000,
        "output_token_limit": 128000,
        "knowledge_cutoff": datetime(2024, 5, 31),
        "supported_service_tier": ["flex", "standard"],
        "pricing": {
            "flex": {
                "input": 0.025,
                "cached_input": 0.0025,
                "output": 0.2,
            },
            "standard": {
                "input": 0.05,
                "cached_input": 0.005,
                "output": 0.4,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "o4-mini": {
        "ids": ["o4-mini", "o4-mini-2025-04-16"],
        "name": "o4-mini",
        "description": "Deprecated Fast, cost-efficient reasoning model, succeeded by GPT-5 mini",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 200000,
        "output_token_limit": 100000,
        "knowledge_cutoff": datetime(2024, 6, 1),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 0.55,
                "cached_input": 0.138,
                "output": 2.2,
            },
            "standard": {
                "input": 1.1,
                "cached_input": 0.275,
                "output": 4.4,
            },
            "priority": {
                "input": 2.0,
                "cached_input": 0.5,
                "output": 8.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "o3": {
        "ids": ["o3", "o3-2025-04-16"],
        "name": "o3",
        "description": "Reasoning model for complex tasks, succeeded by GPT-5",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 200000,
        "output_token_limit": 100000,
        "knowledge_cutoff": datetime(2024, 6, 1),
        "supported_service_tier": ["flex", "standard", "priority"],
        "pricing": {
            "flex": {
                "input": 1.0,
                "cached_input": 0.25,
                "output": 4.0,
            },
            "standard": {
                "input": 2.0,
                "cached_input": 0.5,
                "output": 8.0,
            },
            "priority": {
                "input": 3.5,
                "cached_input": 0.875,
                "output": 14.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "o3-mini": {
        "ids": ["o3-mini", "o3-mini-2025-01-31"],
        "name": "o3-mini",
        "description": "Deprecated A small model alternative to o3",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 200000,
        "output_token_limit": 100000,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 1.1,
                "cached_input": 0.55,
                "output": 4.4,
            },
        },
    },
    "o1-pro": {
        "ids": ["o1-pro", "o1-pro-2025-03-19"],
        "name": "o1-pro",
        "description": "Deprecated Version of o1 with more compute for better responses",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 200000,
        "output_token_limit": 100000,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 150.0,
                "output": 600.0,
            },
        },
    },
    "o1": {
        "ids": ["o1", "o1-2024-12-17"],
        "name": "o1",
        "description": "Deprecated Previous full o-series reasoning model",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 200000,
        "output_token_limit": 100000,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 15.0,
                "cached_input": 7.5,
                "output": 60.0,
            },
        },
    },
    "gpt-4.1": {
        "ids": ["gpt-4.1", "gpt-4.1-2025-04-14"],
        "name": "GPT-4.1",
        "description": "Smartest non-reasoning model",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1047576,
        "output_token_limit": 32768,
        "knowledge_cutoff": datetime(2024, 6, 1),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 2.0,
                "cached_input": 0.5,
                "output": 8.0,
            },
            "priority": {
                "input": 3.5,
                "cached_input": 0.875,
                "output": 14.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-4.1-mini": {
        "ids": ["gpt-4.1-mini", "gpt-4.1-mini-2025-04-14"],
        "name": "GPT-4.1 mini",
        "description": "Smaller, faster version of GPT-4.1",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1047576,
        "output_token_limit": 32768,
        "knowledge_cutoff": datetime(2024, 6, 1),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 0.4,
                "cached_input": 0.1,
                "output": 1.6,
            },
            "priority": {
                "input": 0.7,
                "cached_input": 0.175,
                "output": 2.8,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-4.1-nano": {
        "ids": ["gpt-4.1-nano", "gpt-4.1-nano-2025-04-14"],
        "name": "GPT-4.1 nano",
        "description": "Deprecated Fastest, most cost-efficient version of GPT-4.1",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 1047576,
        "output_token_limit": 32768,
        "knowledge_cutoff": datetime(2024, 6, 1),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 0.1,
                "cached_input": 0.025,
                "output": 0.4,
            },
            "priority": {
                "input": 0.2,
                "cached_input": 0.05,
                "output": 0.8,
            },
        },
    },
    "chatgpt-4o": {
        "ids": ["chatgpt-4o-latest"],
        "name": "ChatGPT-4o",
        "description": "Deprecated GPT-4o model used in ChatGPT",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 16384,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 5.0,
                "output": 15.0,
            },
        },
    },
    "gpt-4o": {
        "ids": ["gpt-4o", "gpt-4o-2024-08-06", "gpt-4o-2024-11-20"],
        "name": "GPT-4o",
        "description": "Deprecated Fast, intelligent, flexible GPT model",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 16384,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 2.5,
                "cached_input": 1.25,
                "output": 10.0,
            },
            "priority": {
                "input": 4.25,
                "cached_input": 2.125,
                "output": 17.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-4o-2024-05-13": {
        "ids": ["gpt-4o-2024-05-13"],
        "name": "GPT-4o",
        "description": "Deprecated Fast, intelligent, flexible GPT model",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 4096,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 5.0,
                "output": 15.0,
            },
        },
    },
    "gpt-4o-mini": {
        "ids": ["gpt-4o-mini", "gpt-4o-mini-2024-07-18"],
        "name": "GPT-4o mini",
        "description": "Fast, affordable small model for focused tasks",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 16384,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "supported_service_tier": ["standard", "priority"],
        "pricing": {
            "standard": {
                "input": 0.15,
                "cached_input": 0.075,
                "output": 0.6,
            },
            "priority": {
                "input": 0.25,
                "cached_input": 0.125,
                "output": 1.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "gpt-4-turbo": {
        "ids": ["gpt-4-turbo", "gpt-4-turbo-2024-04-09"],
        "name": "GPT-4 Turbo",
        "description": "Deprecated An older high-intelligence GPT model",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 4096,
        "knowledge_cutoff": datetime(2023, 12, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 10.0,
                "output": 30.0,
            },
        },
    },
    "gpt-4": {
        "ids": ["gpt-4", "gpt-4-0613"],
        "name": "GPT-4",
        "description": "Deprecated An older high-intelligence GPT model",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 8192,
        "output_token_limit": 8192,
        "knowledge_cutoff": datetime(2023, 12, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 30.0,
                "output": 60.0,
            },
        },
    },
    "gpt-3.5-turbo": {
        "ids": ["gpt-3.5-turbo", "gpt-3.5-turbo-0125"],
        "name": "GPT-3.5 Turbo",
        "description": "Deprecated Legacy GPT model for cheaper chat and non-chat tasks",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 16385,
        "output_token_limit": 4096,
        "knowledge_cutoff": datetime(2021, 9, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 0.5,
                "output": 1.5,
            },
        },
    },
    "gpt-3.5-turbo-1106": {
        "ids": ["gpt-3.5-turbo-1106"],
        "name": "GPT-3.5 Turbo",
        "description": "Deprecated Legacy GPT model for cheaper chat and non-chat tasks",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 16385,
        "output_token_limit": 4096,
        "knowledge_cutoff": datetime(2021, 9, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 1.0,
                "cached_input": 0,
                "output": 2.0,
            },
        },
    },
    "gpt-3.5-turbo-instruct": {
        "ids": ["gpt-3.5-turbo-instruct"],
        "name": "gpt-3.5-turbo-instruct",
        "description": "Deprecated An older model only compatible with the legacy Completions endpoint",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 4096,
        "output_token_limit": 4096,
        "knowledge_cutoff": datetime(2021, 9, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 1.5,
                "cached_input": 0,
                "output": 2.0,
            },
        },
    },
    "codex-mini-latest": {
        "ids": ["codex-mini-latest"],
        "name": "codex-mini-latest",
        "description": "Deprecated Fast reasoning model optimized for the Codex CLI",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["minimal", "low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 200000,
        "output_token_limit": 100000,
        "knowledge_cutoff": datetime(2024, 6, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 1.5,
                "cached_input": 0.375,
                "output": 6.0,
            },
        },
    },
    "o3-pro": {
        "ids": ["o3-pro", "o3-pro-2025-06-10"],
        "name": "o3-pro",
        "description": "Version of o3 with more compute for better responses",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 200000,
        "output_token_limit": 100000,
        "knowledge_cutoff": datetime(2024, 6, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 20.0,
                "output": 80.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "o1-mini": {
        "ids": ["o1-mini", "o1-mini-2024-09-12"],
        "name": "o1-mini",
        "description": "Deprecated A small model alternative to o1",
        "thinking": {
            "thinking": True,
            "thinking_effort": ["low", "medium", "high"],
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 65536,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 1.1,
                "cached_input": 0.55,
                "output": 4.4,
            },
        },
    },
    "gpt-4-1106-vision-preview": {
        "ids": ["gpt-4-1106-vision-preview"],
        "name": "GPT-4 Turbo Vision Preview",
        "description": "Deprecated An older fast GPT model",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 4096,
        "knowledge_cutoff": datetime(2023, 12, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 10.0,
                "output": 30.0,
            },
        },
    },
    "gpt-5.3-chat-latest": {
        "ids": ["gpt-5.3-chat-latest"],
        "name": "GPT-5.3 Chat",
        "description": "Deprecated GPT-5.3 Instant model used in ChatGPT",
        "thinking": {
            "thinking": False,
        },
        "verbosity": {
            "verbosity": True,
            "verbosity_level": ["low", "medium", "high"],
        },
        "temperature": {
            "temperature": True,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": True,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 16384,
        "knowledge_cutoff": datetime(2025, 8, 31),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 1.75,
                "cached_input": 0.175,
                "output": 14.0,
            },
            "native_web_search_tool_call": 0.01,
        },
    },
    "o1-preview": {
        "ids": ["o1-preview", "o1-preview-2024-09-12"],
        "name": "o1 Preview",
        "description": "Deprecated Preview of our first o-series reasoning model",
        "thinking": {
            "thinking": True,
        },
        "verbosity": {
            "verbosity": False,
            "verbosity_level": [],
        },
        "temperature": {
            "temperature": False,
            "thinking_effort_must_be_none": False,
        },
        "top_p": {
            "top_p": False,
            "thinking_effort_must_be_none": False,
        },
        "input_formats": ["text", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": 128000,
        "output_token_limit": 32768,
        "knowledge_cutoff": datetime(2023, 10, 1),
        "supported_service_tier": ["standard"],
        "pricing": {
            "standard": {
                "input": 15.0,
                "output": 60.0,
            },
        },
    },
}




# Remove endpoints that have passed their shutdown date without deleting their
# historical pricing definitions from source control. This also handles groups
# whose rolling alias survives after one dated snapshot is retired.
for _model_group_name in list(OPENAI_MODEL_DICT):
    _model_schema = OPENAI_MODEL_DICT[_model_group_name]
    _active_ids = [
        identifier
        for identifier in _model_schema.get("ids", [])
        if identifier not in OPENAI_SHUT_DOWN_MODEL_IDS
    ]
    if _active_ids:
        _model_schema["ids"] = _active_ids
        _deprecated_ids = {
            identifier: OPENAI_ANNOUNCED_SHUTDOWN_DATES[identifier]
            for identifier in _active_ids
            if identifier in OPENAI_ANNOUNCED_SHUTDOWN_DATES
        }
        if _deprecated_ids:
            _model_schema["deprecated_ids"] = _deprecated_ids
    else:
        del OPENAI_MODEL_DICT[_model_group_name]


# ``chat-latest`` is intentionally unsupported because its backing model,
# capabilities, and price can change without its identifier changing. The
# versioned GPT-5 chat aliases remain in the priced catalog.
OPENAI_LEGACY_MODELS = [
    "babbage-002",
    "davinci-002",
    "chat-latest",
    "gpt-5-search-api-2025-10-14",
    "gpt-5-search-api",
]


# Models in this list have reached their OpenAI shutdown date. Keep the list
# separate from the feature-specific model groups so deprecated identifiers
# cannot accidentally remain selectable through a specialized API workflow.
OPENAI_DEPRECATED_MODELS = [
    *sorted(OPENAI_SHUT_DOWN_MODEL_IDS),
    "gpt-4-0314",
    "gpt-4-1106-preview",
    "gpt-4-0125-preview",
    "gpt-4-turbo-preview",
    "gpt-4-turbo-preview-completions",
    "gpt-4o-realtime-preview",
    "gpt-4o-realtime-preview-2025-06-03",
    "gpt-4o-realtime-preview-2024-12-17",
    "gpt-4o-mini-realtime-preview",
    "gpt-4o-audio-preview",
    "gpt-4o-mini-audio-preview",
]



OPENAI_EMBEDDING_MODELS = ["text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002"]


OPENAI_MODERATION_MODELS = [
    "omni-moderation-latest",
    "omni-moderation",
    "omni-moderation-2024-09-26",
    "text-moderation-latest",
    "text-moderation",
    "text-moderation-007",
    "text-moderation-stable",
]



OPENAI_IMAGE_GENERATION_MODELS = [
    "gpt-image-2",
    "gpt-image-2-2026-04-21",
    "gpt-image-1.5",
    "gpt-image-1.5-2025-12-16",
    "chatgpt-image-latest",
    "gpt-image-1",
    "gpt-image-1-mini",
    "dall-e-3",
    "dall-e-2",
]



OPENAI_SEARCH_CHAT_COMPLETIONS_MODELS = [
    "gpt-4o-mini-search-preview",
    "gpt-4o-mini-search-preview-2025-03-11",
    "gpt-4o-search-preview",
    "gpt-4o-search-preview-2025-03-11",
]



# Video-only IDs remain excluded from chat, image, transcription, and realtime
# catalogs even though Omlorix no longer offers native OpenAI video generation.
OPENAI_VIDEO_ONLY_MODELS = ["sora-2", "sora-2-2025-12-08", "sora-2-2025-10-06", "sora-2-pro", "sora-2-pro-2025-10-06"]


OPENAI_TTS_MODELS = [
    "gpt-4o-mini-tts",
    "gpt-4o-mini-tts-2025-12-15",
    "gpt-4o-mini-tts-2025-03-20",
    "tts-1",
    "tts-1-hd",
    "tts-1-1106",
    "tts-1-hd-1106",
]



OPENAI_TRANSCRIPTION_MODELS = [
    "gpt-transcribe",
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "gpt-4o-mini-transcribe-2025-12-15",
    "gpt-4o-mini-transcribe-2025-03-20",
    "gpt-4o-transcribe-diarize",
    "whisper-1",
]


# Live transcription models use the Realtime transcription protocol. They are
# deliberately separate from both completed-file transcription and two-way
# speech models so each settings picker only exposes models its runtime can use.
OPENAI_LIVE_TRANSCRIPTION_MODELS = [
    "gpt-live-transcribe",
]

# This older identifier is also transcription-only. Omlorix does not offer it
# for new live dictation settings, but it must not leak into chat or voice-call
# model lists when an upstream /models response still contains it.
OPENAI_REALTIME_TRANSCRIPTION_ONLY_MODELS = [
    *OPENAI_LIVE_TRANSCRIPTION_MODELS,
    "gpt-realtime-whisper",
]



OPENAI_REALTIME_MODELS = [
    "gpt-realtime-1.5",
    "gpt-realtime",
    "gpt-realtime-2025-08-28",
    "gpt-realtime-mini",
    "gpt-realtime-mini-2025-12-15",
    "gpt-realtime-mini-2025-10-06",
    "gpt-4o-realtime-preview-2024-10-01",
    "gpt-4o-mini-realtime-preview-2024-12-17",
    "gpt-realtime-2.1",
    "gpt-realtime-2.1-mini",
    "gpt-realtime-2",
    "gpt-realtime-translate",
]


OPENAI_AUDIO_MODELS = [
    "gpt-audio-1.5",
    "gpt-audio",
    "gpt-audio-2025-08-28",
    "gpt-audio-mini",
    "gpt-audio-mini-2025-12-15",
    "gpt-audio-mini-2025-10-06",
    "gpt-4o-audio-preview-2025-06-03",
    "gpt-4o-audio-preview-2024-12-17",
    "gpt-4o-audio-preview-2024-10-01",
    "gpt-4o-mini-audio-preview-2024-12-17",
]




OPENAI_COMPLETION_MODELS = [
    "gpt-5.6-sol",
    "gpt-5.6",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5-pro",
    "gpt-5.5-pro-2026-04-23",
    "gpt-5.5",
    "gpt-5.5-2026-04-23",
    "gpt-5.4-mini",
    "gpt-5.4-mini-2026-03-17",
    "gpt-5.4-nano",
    "gpt-5.4-nano-2026-03-17",
    "gpt-5.4-pro",
    "gpt-5.4-pro-2026-03-05",
    "gpt-5.4",
    "gpt-5.4-2026-03-05",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.2-pro",
    "gpt-5.2-pro-2025-12-11",
    "gpt-5.2-chat-latest",
    "gpt-5.2",
    "gpt-5.2-2025-12-11",
    "gpt-5.1",
    "gpt-5.1-2025-11-13",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5.1-chat-latest",
    "gpt-5-pro",
    "gpt-5-pro-2025-10-06",
    "gpt-5-chat-latest",
    "gpt-5",
    "gpt-5-2025-08-07",
    "gpt-5-codex",
    "gpt-5-mini",
    "gpt-5-mini-2025-08-07",
    "gpt-5-nano",
    "gpt-5-nano-2025-08-07",
    "o4-mini",
    "o4-mini-2025-04-16",
    "o3",
    "o3-2025-04-16",
    "o3-mini",
    "o3-mini-2025-01-31",
    "o1-pro",
    "o1-pro-2025-03-19",
    "o1",
    "o1-2024-12-17",
    "gpt-4.1",
    "gpt-4.1-2025-04-14",
    "gpt-4.1-mini",
    "gpt-4.1-mini-2025-04-14",
    "gpt-4.1-nano",
    "gpt-4.1-nano-2025-04-14",
    "chatgpt-4o-latest",
    "gpt-4o",
    "gpt-4o-2024-08-06",
    "gpt-4o-2024-11-20",
    "gpt-4o-2024-05-13",
    "gpt-4o-mini",
    "gpt-4o-mini-2024-07-18",
    "gpt-4-turbo",
    "gpt-4-turbo-2024-04-09",
    "gpt-4",
    "gpt-4-0613",
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-0125",
    "gpt-3.5-turbo-1106",
    "gpt-3.5-turbo-instruct",
    "codex-mini-latest",
    "o3-pro",
    "o3-pro-2025-06-10",
    "o1-mini",
    "o1-mini-2024-09-12",
    "gpt-4-1106-vision-preview",
    "gpt-5.3-chat-latest",
    "o1-preview",
    "o1-preview-2024-09-12",
]

# Keep this list mechanically aligned with the authoritative pricing catalog.
# Hand-maintained copies previously left shut-down IDs selectable for weeks.
OPENAI_COMPLETION_MODELS = sorted(
    {
        identifier
        for model_schema in OPENAI_MODEL_DICT.values()
        for identifier in model_schema.get("ids", [])
    }
)

# These model IDs must remain excluded from API-provided model listings.  They
# are intentionally absent from ``OPENAI_MODEL_DICT`` because Omlorix no longer
# supports their native Deep Research execution path, but compatible endpoints
# can continue returning them from ``/models`` for some time after removal.
OPENAI_REMOVED_DEEP_RESEARCH_MODELS = [
    "o3-deep-research",
    "o3-deep-research-2025-06-26",
    "o4-mini-deep-research",
    "o4-mini-deep-research-2025-06-26",
]



OPENAI_UNSUPPORTED_MODELS = (
    OPENAI_LEGACY_MODELS
    + OPENAI_DEPRECATED_MODELS
    + OPENAI_EMBEDDING_MODELS
    + OPENAI_MODERATION_MODELS
    + OPENAI_IMAGE_GENERATION_MODELS
    + OPENAI_SEARCH_CHAT_COMPLETIONS_MODELS
    + OPENAI_VIDEO_ONLY_MODELS
    + OPENAI_TTS_MODELS
    + OPENAI_TRANSCRIPTION_MODELS
    + OPENAI_REALTIME_TRANSCRIPTION_ONLY_MODELS
    + OPENAI_REALTIME_MODELS
    + OPENAI_AUDIO_MODELS
    + OPENAI_REMOVED_DEEP_RESEARCH_MODELS
)



OPENAI_UNSUPPORTED_IMAGE_GENERATION_MODELS = (
    OPENAI_LEGACY_MODELS
    + OPENAI_DEPRECATED_MODELS
    + OPENAI_EMBEDDING_MODELS
    + OPENAI_MODERATION_MODELS
    + OPENAI_SEARCH_CHAT_COMPLETIONS_MODELS
    + OPENAI_VIDEO_ONLY_MODELS
    + OPENAI_TTS_MODELS
    + OPENAI_TRANSCRIPTION_MODELS
    + OPENAI_REALTIME_TRANSCRIPTION_ONLY_MODELS
    + OPENAI_REALTIME_MODELS
    + OPENAI_AUDIO_MODELS
    + OPENAI_COMPLETION_MODELS
    + OPENAI_REMOVED_DEEP_RESEARCH_MODELS
    )


OPENAI_UNSUPPORTED_TRANSCRIPTION_MODELS = (
    OPENAI_LEGACY_MODELS
    + OPENAI_DEPRECATED_MODELS
    + OPENAI_EMBEDDING_MODELS
    + OPENAI_MODERATION_MODELS
    + OPENAI_IMAGE_GENERATION_MODELS
    + OPENAI_VIDEO_ONLY_MODELS
    + OPENAI_SEARCH_CHAT_COMPLETIONS_MODELS
    + OPENAI_TTS_MODELS
    + OPENAI_REALTIME_TRANSCRIPTION_ONLY_MODELS
    + OPENAI_REALTIME_MODELS
    + OPENAI_AUDIO_MODELS
    + OPENAI_COMPLETION_MODELS
    + OPENAI_REMOVED_DEEP_RESEARCH_MODELS
    )


OPENAI_UNSUPPORTED_REALTIME_MODELS = (
    OPENAI_LEGACY_MODELS
    + OPENAI_DEPRECATED_MODELS
    + OPENAI_EMBEDDING_MODELS
    + OPENAI_MODERATION_MODELS
    + OPENAI_IMAGE_GENERATION_MODELS
    + OPENAI_VIDEO_ONLY_MODELS
    + OPENAI_SEARCH_CHAT_COMPLETIONS_MODELS
    + OPENAI_TTS_MODELS
    + OPENAI_TRANSCRIPTION_MODELS
    + OPENAI_REALTIME_TRANSCRIPTION_ONLY_MODELS
    + OPENAI_AUDIO_MODELS
    + OPENAI_COMPLETION_MODELS
    + OPENAI_REMOVED_DEEP_RESEARCH_MODELS
)
