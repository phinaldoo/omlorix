from datetime import datetime


# Verified against Google's model, pricing, release-note, and deprecation
# documentation on 2026-07-27.
GOOGLE_AISTUDIO_CATALOG_LAST_VERIFIED = "2026-07-27"
GOOGLE_AISTUDIO_MODELS_DOCS_URL = "https://ai.google.dev/gemini-api/docs/models"
GOOGLE_AISTUDIO_PRICING_DOCS_URL = "https://ai.google.dev/gemini-api/docs/pricing"

AISTUDIO_MODEL_DICT = {
    "gemini-3.6-flash": {
        "ids": ["gemini-3.6-flash"],
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": True,
            "reasoning_effort_support": True,
            "reasoning_effort": ["minimal", "low", "medium", "high"],
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 1.50,
            "input_text_200k": 1.50,
            "input_audio": 1.50,
            "input_audio_200k": 1.50,
            "input_image": 1.50,
            "input_image_200k": 1.50,
            "input_video": 1.50,
            "input_video_200k": 1.50,
            "cached_input_text": 0.15,
            "cached_input_text_200k": 0.15,
            "cached_input_audio": 0.15,
            "cached_input_audio_200k": 0.15,
            "cached_input_image": 0.15,
            "cached_input_image_200k": 0.15,
            "cached_input_video": 0.15,
            "cached_input_video_200k": 0.15,
            "output": 7.50,
            "output_200k": 7.50,
        },
    },
    "gemini-3.5-flash": {
        "ids": ["gemini-3.5-flash"],
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": True,
            "reasoning_effort_support": True,
            "reasoning_effort": ["minimal", "low", "medium", "high"],
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 1.50, # input text less or equal to 200k tokens
            "input_text_200k": 1.50, # input text if more than 200k tokens
            "input_audio": 1.50,
            "input_audio_200k": 1.50,
            "input_image": 1.50,
            "input_image_200k": 1.50,
            "input_video": 1.50,
            "input_video_200k": 1.50,
            "cached_input_text": 0.15,
            "cached_input_text_200k": 0.15,
            "cached_input_audio": 0.15,
            "cached_input_audio_200k": 0.15,
            "cached_input_image": 0.15,
            "cached_input_image_200k": 0.15,
            "cached_input_video": 0.15,
            "cached_input_video_200k": 0.15,
            "output": 9.00,
            "output_200k": 9.00,
        }
    },
    "gemini-3.5-flash-lite": {
        "ids": ["gemini-3.5-flash-lite"],
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": True,
            "reasoning_effort_support": True,
            "reasoning_effort": ["minimal", "low", "medium", "high"],
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 0.30,
            "input_text_200k": 0.30,
            "input_audio": 0.30,
            "input_audio_200k": 0.30,
            "input_image": 0.30,
            "input_image_200k": 0.30,
            "input_video": 0.30,
            "input_video_200k": 0.30,
            "cached_input_text": 0.03,
            "cached_input_text_200k": 0.03,
            "cached_input_audio": 0.03,
            "cached_input_audio_200k": 0.03,
            "cached_input_image": 0.03,
            "cached_input_image_200k": 0.03,
            "cached_input_video": 0.03,
            "cached_input_video_200k": 0.03,
            "output": 2.50,
            "output_200k": 2.50,
        },
    },
    "gemini-3.1-pro": {
        "ids": [
            "gemini-3.1-pro-preview",
            "gemini-3.1-pro-preview-customtools",
        ],
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": True,
            "reasoning_effort_support": True,
            "reasoning_effort": ["low", "medium", "high"],
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 2.00, # input text less or equal to 200k tokens
            "input_text_200k": 4.00, # input text if more than 200k tokens
            "input_audio": 2.00,
            "input_audio_200k": 4.00,
            "input_image": 2.00,
            "input_image_200k": 4.00,
            "input_video": 2.00,
            "input_video_200k": 4.00,
            "cached_input_text": 0.20,
            "cached_input_text_200k": 0.40,
            "cached_input_audio": 0.20,
            "cached_input_audio_200k": 0.40,
            "cached_input_image": 0.20,
            "cached_input_image_200k": 0.40,
            "cached_input_video": 0.20,
            "cached_input_video_200k": 0.40,
            "output": 12.00,
            "output_200k": 18.00,
        }
    },
    "gemini-3.1-flash-lite": {
        "ids": ["gemini-3.1-flash-lite"],
        "deprecated": True,
        "shutdown_date": "2027-05-07",
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": True,
            "reasoning_effort_support": True,
            "reasoning_effort": ["minimal", "low", "medium", "high"],
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 0.25, # input text less or equal to 200k tokens
            "input_text_200k": 0.25, # input text if more than 200k tokens
            "input_audio": 0.50,
            "input_audio_200k": 0.50,
            "input_image": 0.25,
            "input_image_200k": 0.25,
            "input_video": 0.25,
            "input_video_200k": 0.25,
            "cached_input_text": 0.025,
            "cached_input_text_200k": 0.025,
            "cached_input_audio": 0.05,
            "cached_input_audio_200k": 0.05,
            "cached_input_image": 0.025,
            "cached_input_image_200k": 0.025,
            "cached_input_video": 0.025,
            "cached_input_video_200k": 0.025,
            "output": 1.50,
            "output_200k": 1.50,
        }
    },
    "gemini-3-flash": {
        "ids": ["gemini-3-flash-preview"],
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": True,
            "reasoning_effort_support": True,
            "reasoning_effort": ["minimal", "low", "medium", "high"],
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 0.50, # input text less or equal to 200k tokens
            "input_text_200k": 0.50, # input text if more than 200k tokens
            "input_audio": 1.00,
            "input_audio_200k": 1.00,
            "input_image": 0.50,
            "input_image_200k": 0.50,
            "input_video": 0.50,
            "input_video_200k": 0.50,
            "cached_input_text": 0.05,
            "cached_input_text_200k": 0.05,
            "cached_input_audio": 0.10,
            "cached_input_audio_200k": 0.10,
            "cached_input_image": 0.05,
            "cached_input_image_200k": 0.05,
            "cached_input_video": 0.05,
            "cached_input_video_200k": 0.05,
            "output": 3.00,
            "output_200k": 3.00,
        }
    },
    "gemini-2.5-pro": {
        "ids": ["gemini-2.5-pro"],
        "deprecated": True,
        "shutdown_date": "2026-10-16",
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": True,
            "thinking_budget_min": 128,
            "thinking_budget_max": 32768,
            "thinking_support_dynamic": True,
            "reasoning_effort_support": False,
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 1.25, # input text less or equal to 200k tokens
            "input_text_200k": 2.50, # input text if more than 200k tokens
            "input_audio": 1.25,
            "input_audio_200k": 2.50,
            "input_image": 1.25,
            "input_image_200k": 2.50,
            "input_video": 1.25,
            "input_video_200k": 2.50,
            "cached_input_text": 0.125,
            "cached_input_text_200k": 0.25,
            "cached_input_audio": 0.125,
            "cached_input_audio_200k": 0.25,
            "cached_input_image": 0.125,
            "cached_input_image_200k": 0.25,
            "cached_input_video": 0.125,
            "cached_input_video_200k": 0.25,
            "output": 10.00,
            "output_200k": 15.00,
        }
    },
    "gemini-2.5-flash": {
        "ids": ["gemini-2.5-flash"],
        "deprecated": True,
        "shutdown_date": "2026-10-16",
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": True,
            "thinking_budget_support": True,
            "thinking_budget_min": 0,
            "thinking_budget_max": 24576,
            "thinking_support_dynamic": True,
            "reasoning_effort_support": False
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 0.30, # input text less or equal to 200k tokens
            "input_text_200k": 0.30, # input text if more than 200k tokens
            "input_audio": 1.00,
            "input_audio_200k": 1.00,
            "input_image": 0.30,
            "input_image_200k": 0.30,
            "input_video": 0.30,
            "input_video_200k": 0.30,
            "cached_input_text": 0.03,
            "cached_input_text_200k": 0.03,
            "cached_input_audio": 0.10,
            "cached_input_audio_200k": 0.10,
            "cached_input_image": 0.03,
            "cached_input_image_200k": 0.03,
            "cached_input_video": 0.03,
            "cached_input_video_200k": 0.03,
            "output": 2.50,
            "output_200k": 2.50,
        }
    },
    "gemini-2.5-flash-lite": {
        "ids": ["gemini-2.5-flash-lite"],
        "deprecated": True,
        "shutdown_date": "2026-10-16",
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": True,
            "thinking_budget_support": True,
            "thinking_budget_min": 512,
            "thinking_budget_max": 24576,
            "thinking_support_dynamic": True,
            "reasoning_effort_support": False
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 0.10, # input text less or equal to 200k tokens
            "input_text_200k": 0.10, # input text if more than 200k tokens
            "input_audio": 0.30,
            "input_audio_200k": 0.30,
            "input_image": 0.10,
            "input_image_200k": 0.10,
            "input_video": 0.10,
            "input_video_200k": 0.10,
            "cached_input_text": 0.01,
            "cached_input_text_200k": 0.01,
            "cached_input_audio": 0.03,
            "cached_input_audio_200k": 0.03,
            "cached_input_image": 0.01,
            "cached_input_image_200k": 0.01,
            "cached_input_video": 0.01,
            "cached_input_video_200k": 0.01,
            "output": 0.40,
            "output_200k": 0.40,
        }
    },
    "gemma-4-31b": {
        "ids": ["gemma-4-31b-it"],
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": False,
            "reasoning_effort_support": True,
            "reasoning_effort": ["minimal", "high"],
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 0,
            "input_text_200k": 0,
            "input_audio": 0,
            "input_audio_200k": 0,
            "input_image": 0,
            "input_image_200k": 0,
            "input_video": 0,
            "input_video_200k": 0,
            "output": 0,
            "output_200k": 0,
        }
    },
    "gemma-3-1b-it": {
        "ids": ["gemma-3-1b-it"],
        "supports_native_websearch": False,
        "thinking": {
            "thinking": False,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": False,
            "reasoning_effort_support": False,
        },
        "knowledge_cutoff": datetime(2024, 8, 1),
        "support_media_resolution": False,
        "pricing": {
            "input_text": 0,
            "input_text_200k": 0,
            "input_audio": 0,
            "input_audio_200k": 0,
            "input_image": 0,
            "input_image_200k": 0,
            "input_video": 0,
            "input_video_200k": 0,
            "output": 0,
            "output_200k": 0,
        }
    },
    "gemma-4-26b-a4b-it": {
        "ids": ["gemma-4-26b-a4b-it"],
        "supports_native_websearch": True,
        "thinking": {
            "thinking": True,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": False,
            "reasoning_effort_support": True,
            "reasoning_effort": ["minimal", "high"],
        },
        "knowledge_cutoff": datetime(2025, 1, 1),
        "support_media_resolution": True,
        "pricing": {
            "input_text": 0,
            "input_text_200k": 0,
            "input_audio": 0,
            "input_audio_200k": 0,
            "input_image": 0,
            "input_image_200k": 0,
            "input_video": 0,
            "input_video_200k": 0,
            "output": 0,
            "output_200k": 0,
        }
    },
    "gemma-3-4b-it": {
        "ids": ["gemma-3-4b-it"],
        "supports_native_websearch": False,
        "thinking": {
            "thinking": False,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": False,
            "reasoning_effort_support": False,
        },
        "knowledge_cutoff": datetime(2024, 8, 1),
        "support_media_resolution": False,
        "pricing": {
            "input_text": 0,
            "input_text_200k": 0,
            "input_audio": 0,
            "input_audio_200k": 0,
            "input_image": 0,
            "input_image_200k": 0,
            "input_video": 0,
            "input_video_200k": 0,
            "output": 0,
            "output_200k": 0,
        }
    },
    "gemma-3-12b-it": {
        "ids": ["gemma-3-12b-it"],
        "supports_native_websearch": False,
        "thinking": {
            "thinking": False,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": False,
            "reasoning_effort_support": False,
        },
        "knowledge_cutoff": datetime(2024, 8, 1),
        "support_media_resolution": False,
        "pricing": {
            "input_text": 0,
            "input_text_200k": 0,
            "input_audio": 0,
            "input_audio_200k": 0,
            "input_image": 0,
            "input_image_200k": 0,
            "input_video": 0,
            "input_video_200k": 0,
            "output": 0,
            "output_200k": 0,
        }
    },
    "gemma-3-27b-it": {
        "ids": ["gemma-3-27b-it"],
        "supports_native_websearch": False,
        "thinking": {
            "thinking": False,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": False,
            "reasoning_effort_support": False,
        },
        "knowledge_cutoff": datetime(2024, 8, 1),
        "support_media_resolution": False,
        "pricing": {
            "input_text": 0,
            "input_text_200k": 0,
            "input_audio": 0,
            "input_audio_200k": 0,
            "input_image": 0,
            "input_image_200k": 0,
            "input_video": 0,
            "input_video_200k": 0,
            "output": 0,
            "output_200k": 0,
        }
    },
    "gemma-3n-e2b-it": {
        "ids": ["gemma-3n-e2b-it"],
        "supports_native_websearch": False,
        "thinking": {
            "thinking": False,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": False,
            "reasoning_effort_support": False,
        },
        "knowledge_cutoff": datetime(2024, 8, 1),
        "support_media_resolution": False,
        "pricing": {
            "input_text": 0,
            "input_text_200k": 0,
            "input_audio": 0,
            "input_audio_200k": 0,
            "input_image": 0,
            "input_image_200k": 0,
            "input_video": 0,
            "input_video_200k": 0,
            "output": 0,
            "output_200k": 0,
        }
    },
    "gemma-3n-e4b-it": {
        "ids": ["gemma-3n-e4b-it"],
        "supports_native_websearch": False,
        "thinking": {
            "thinking": False,
            "thinking_disabled_allowed": False,
            "thinking_budget_support": False,
            "thinking_budget_min": 0,
            "thinking_budget_max": 0,
            "thinking_support_dynamic": False,
            "reasoning_effort_support": False,
        },
        "knowledge_cutoff": datetime(2024, 8, 1),
        "support_media_resolution": False,
        "pricing": {
            "input_text": 0,
            "input_text_200k": 0,
            "input_audio": 0,
            "input_audio_200k": 0,
            "input_image": 0,
            "input_image_200k": 0,
            "input_video": 0,
            "input_video_200k": 0,
            "output": 0,
            "output_200k": 0,
        }
    },
}



AISTUDIO_MODELS_NOT_SUPPORTED = [
    # Shut down endpoints are ignored if an API/account still returns stale
    # discovery metadata for them.
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
    "gemini-2.5-computer-use-preview-10-2025",
    "gemini-robotics-er-1.5-preview",
    "nano-banana-pro-preview",
    # Rolling aliases can change their backing model and price without changing
    # their identifier, so they must not inherit a static catalog entry.
    "gemini-flash-latest",
    # This rolling alias has no dated mapping in the current release notes.
    # Excluding it avoids silently applying a stale family's static price.
    "gemini-flash-lite-latest",
    # Google's last announced target for this alias was the now-shut-down
    # gemini-3-pro-preview. Do not assume 3.1 Pro pricing until a new mapping is
    # documented.
    "gemini-pro-latest",
    "gemini-3-pro-image-preview", 
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
    "gemini-3-pro-image",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image",
    "lyria-3-clip-preview",
    "lyria-3-pro-preview",
    "deep-research-pro-preview-12-2025",
    "aqa",
    "gemini-3-pro-preview",
    "gemini-omni-flash-preview",
    "gemini-3.1-flash-tts-preview",
    "gemini-robotics-er-1.6-preview",
    "antigravity-preview-05-2026",
    "deep-research-max-preview-04-2026",
    "deep-research-preview-04-2026"
]


GOOGLE_AISTUDIO_DEEP_RESEARCH_MODELS = [
    "deep-research-preview-04-2026",
    "deep-research-max-preview-04-2026",
]



GOOGLE_AISTUDIO_VIDEO_GENERATION_MODELS = [
    {
        "name": "Veo 3.1",
        "ids": ["veo-3.1-generate-preview"],
        "resolution_support": True,
        "aspect_ratio": ["16:9", "9:16"],
        "resolution_supported": ["720p", "1080p", "4k"],
        "duration_seconds": ["4", "6", "8"],
        "pricing": {
            "per_second": {
                "720p": 0.40,
                "1080p": 0.40,
                "4k": 0.60,
            },
        },
    },
    {
        "name": "Veo 3.1 Lite",
        "ids": ["veo-3.1-lite-generate-preview"],
        "resolution_support": True,
        "aspect_ratio": ["16:9", "9:16"],
        "resolution_supported": ["720p", "1080p"],
        "duration_seconds": ["4", "6", "8"],
        "pricing": {
            "per_second": {
                "720p": 0.05,
                "1080p": 0.08,
            },
        },
    },
    {
        "name": "Veo 3.1 Fast",
        "ids": ["veo-3.1-fast-generate-preview"],
        "resolution_support": True,
        "aspect_ratio": ["16:9", "9:16"],
        "resolution_supported": ["720p", "1080p", "4k"],
        "duration_seconds": ["4", "6", "8"],
        "pricing": {
            "per_second": {
                "720p": 0.10,
                "1080p": 0.12,
                "4k": 0.30,
            },
        },
    },
]

GOOGLE_AISTUDIO_VIDEO_PRICING_DOCS_URL = "https://ai.google.dev/gemini-api/docs/pricing"
GOOGLE_AISTUDIO_VIDEO_PRICING_DOCS_LAST_UPDATED = "2026-07-09"




GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_URL = "https://ai.google.dev/gemini-api/docs/pricing"
GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_LAST_UPDATED = "2026-07-09"

GOOGLE_AISTUDIO_MUSIC_GENERATION_MODELS = [
    {
        "name": "Lyria 3 Clip",
        "ids": ["lyria-3-clip-preview"],
        "description": "Fast 30-second clips for loops, previews, and prompt iteration.",
        "response_formats": ["mp3"],
        "supports_reference_images": True,
        "max_reference_images": 10,
        "duration_label": "30 seconds",
        "pricing": {
            "request": 0.04,
            "currency": "USD",
            "unit": "song",
            "source_url": GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_URL,
            "source_last_updated": GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_LAST_UPDATED,
        },
    },
    {
        "name": "Lyria 3 Pro",
        "ids": ["lyria-3-pro-preview"],
        "description": "Full-length songs with verses, choruses, bridges, vocals, and richer arrangements.",
        "response_formats": ["mp3", "wav"],
        "supports_reference_images": True,
        "max_reference_images": 10,
        "duration_label": "Full song",
        "pricing": {
            "request": 0.08,
            "currency": "USD",
            "unit": "song",
            "source_url": GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_URL,
            "source_last_updated": GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_LAST_UPDATED,
        },
    },
]


GOOGLE_LIVE_MODELS = [
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-3.1-flash-live-preview",
]





IMAGE_GEN_MODELS = [
    {
        "name": "Gemini 2.5 Flash Image (Nano Banana)",
        "ids": ["gemini-2.5-flash-image"],
        "deprecated": True,
        "shutdown_date": "2026-10-02",
        "resolution": ["1K"],
        "aspect_ratio": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "category": "model",
    },
    {
        "name": "Gemini 3 Pro Image (Nano Banana Pro)",
        "ids": ["gemini-3-pro-image"],
        "resolution": ["1K", "2K", "4K"],
        "aspect_ratio": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "category": "model",
    },
    {
        "name": "Gemini 3.1 Flash Image (Nano Banana 2)",
        "ids": ["gemini-3.1-flash-image"],
        "resolution": ["512px", "1K", "2K", "4K"],
        "aspect_ratio": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "category": "model",
    },
    {
        "name": "Gemini 3.1 Flash Lite Image (Nano Banana 2 Lite)",
        "ids": ["gemini-3.1-flash-lite-image"],
        "resolution": ["1K"],
        "aspect_ratio": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "category": "model",
    },
    {
        "name": "Imagen 4",
        "ids": ["imagen-4.0-generate-001"],
        "deprecated": True,
        "shutdown_date": "2026-08-17",
        "category": "imagen",
        "support_imageSize": True,
        "supported_imageSize": ["1K", "2K"],
        "aspectRatio": ["1:1", "3:4", "4:3", "9:16", "16:9"],
    },
    {
        "name": "Imagen 4 Fast",
        "ids": ["imagen-4.0-fast-generate-001"],
        "deprecated": True,
        "shutdown_date": "2026-08-17",
        "category": "imagen",
        "support_imageSize": False,
        "supported_imageSize": [],
        "aspectRatio": ["1:1", "3:4", "4:3", "9:16", "16:9"],
    },
    {
        "name": "Imagen 4 Ultra",
        "ids": ["imagen-4.0-ultra-generate-001"],
        "deprecated": True,
        "shutdown_date": "2026-08-17",
        "category": "imagen",
        "support_imageSize": True,
        "supported_imageSize": ["1K", "2K"],
        "aspectRatio": ["1:1", "3:4", "4:3", "9:16", "16:9"],
    },
]



GOOGLE_EMBEDDING_MODELS = [
    "gemini-embedding-001",
    "gemini-embedding-2",
]
