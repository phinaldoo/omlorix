"""Schemas for login-page customization settings."""

from typing import Any, Literal

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, conint, field_validator


class LoginCustomizationSettings(BaseModel):
    show_custom_logo: bool
    custom_logo_height: Literal["small", "medium", "big"]
    # Login design layout settings
    login_design: Literal[
        "classic", "split", "split_image", "centered", "minimal", "glass"
    ] = "classic"
    show_background_image: bool = False
    background_image_fit: Literal["cover", "contain", "custom_percent"] = "cover"
    background_image_size_percent: conint(ge=10, le=300) = 100
    background_overlay_opacity: int = 40
    design_background_color: str = "#ffffff"
    branding_title: str = "Welcome back"
    branding_subtitle: str = "Sign in to continue to your account"
    show_branding_text: bool = True

    @field_validator("design_background_color", mode="before")
    @classmethod
    def _normalize_design_background_color(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "#ffffff"
        normalized = text if text.startswith("#") else f"#{text}"
        if len(normalized) == 4:
            normalized = "#" + "".join(ch * 2 for ch in normalized[1:])
        if len(normalized) != 7:
            raise ValueError("Background color must be a 6-digit hex value.")
        try:
            int(normalized[1:], 16)
        except ValueError as exc:
            raise ValueError("Background color must be a valid hex color.") from exc
        return normalized.lower()


# -------------------
# Login Customization Settings Schema
# -------------------
login_customization_schema = Sections(
    sections=[
        Section(
            title="Design",
            description="Customize layout, visual style, and background behavior for your login page.",
            i18n_title="schema_login_customization_sec1_title",
            i18n_description="schema_login_customization_sec1_desc",
            fields=[
                FieldSchema(
                    key="login_design",
                    label="Login page design",
                    description="Select the visual layout for the login experience.",
                    type="select",
                    options=[
                        {
                            "value": "classic",
                            "label": "Classic",
                            "i18n_label": "schema_option_design_classic",
                        },
                        {
                            "value": "split",
                            "label": "Split (Gradient)",
                            "i18n_label": "schema_option_design_split",
                        },
                        {
                            "value": "split_image",
                            "label": "Split (Image)",
                            "i18n_label": "schema_option_design_split_image",
                        },
                        {
                            "value": "centered",
                            "label": "Centered Card",
                            "i18n_label": "schema_option_design_centered",
                        },
                        {
                            "value": "minimal",
                            "label": "Minimal",
                            "i18n_label": "schema_option_design_minimal",
                        },
                        {
                            "value": "glass",
                            "label": "Glassmorphism",
                            "i18n_label": "schema_option_design_glass",
                        },
                    ],
                    i18n_label="schema_login_customization_login_design",
                    i18n_description="schema_login_customization_login_design_desc",
                ),
                FieldSchema(
                    key="show_background_image",
                    label="Show background image",
                    description="Display a custom background image on the login page (upload in General > Logo).",
                    type="boolean",
                    dependency="login_design",
                    dependency_value=["split_image"],
                    i18n_label="schema_login_customization_show_bg_image",
                ),
                FieldSchema(
                    key="background_image_fit",
                    label="Background image fit",
                    description="Choose how the background image should fill the available space.",
                    type="select",
                    options=[
                        {
                            "value": "cover",
                            "label": "Cover",
                            "i18n_label": "schema_option_bg_fit_cover",
                        },
                        {
                            "value": "contain",
                            "label": "Contain",
                            "i18n_label": "schema_option_bg_fit_contain",
                        },
                        {
                            "value": "custom_percent",
                            "label": "Custom (%)",
                            "i18n_label": "schema_option_bg_fit_custom_percent",
                        },
                    ],
                    dependency="show_background_image",
                    dependency_value=True,
                    dependency2="login_design",
                    dependency2_value=["split_image"],
                    i18n_label="schema_login_customization_bg_fit",
                    i18n_description="schema_login_customization_bg_fit_desc",
                ),
                FieldSchema(
                    key="background_image_size_percent",
                    label="Background image size (%)",
                    description="Scale for the custom image fit mode. 100% is default, lower values make the image smaller.",
                    type="number",
                    min_value=10,
                    max_value=300,
                    dependency="background_image_fit",
                    dependency_value="custom_percent",
                    dependency2="login_design",
                    dependency2_value=["split_image"],
                    i18n_label="schema_login_customization_bg_size_percent",
                    i18n_description="schema_login_customization_bg_size_percent_desc",
                ),
                FieldSchema(
                    key="show_branding_text",
                    label="Show branding message",
                    description="Display a customizable welcome message on the login page.",
                    type="boolean",
                    dependency="login_design",
                    dependency_value=["split", "split_image"],
                    i18n_label="schema_login_customization_show_branding_text",
                    i18n_description="schema_login_customization_show_branding_text_desc",
                ),
                FieldSchema(
                    key="branding_title",
                    label="Welcome headline",
                    description="The main headline visitors see when they arrive at the login page.",
                    type="string",
                    placeholder="Welcome back",
                    max_length=60,
                    dependency="show_branding_text",
                    dependency_value=True,
                    dependency2="login_design",
                    dependency2_value=["split", "split_image"],
                    i18n_label="schema_login_customization_branding_title",
                    i18n_description="schema_login_customization_branding_title_desc",
                ),
                FieldSchema(
                    key="branding_subtitle",
                    label="Welcome message",
                    description="Supporting text that appears below the headline. Use this to guide users or reinforce your brand.",
                    type="textarea",
                    placeholder="Sign in to continue to your account",
                    max_length=200,
                    rows=3,
                    dependency="show_branding_text",
                    dependency_value=True,
                    dependency2="login_design",
                    dependency2_value=["split", "split_image"],
                    i18n_label="schema_login_customization_branding_subtitle",
                    i18n_description="schema_login_customization_branding_subtitle_desc",
                ),
                FieldSchema(
                    key="background_overlay_opacity",
                    label="Background overlay opacity",
                    description="Darken the background image for better text readability (0-100%).",
                    type="number",
                    min_value=0,
                    max_value=100,
                    dependency="show_background_image",
                    dependency_value=True,
                    i18n_label="schema_login_customization_bg_overlay",
                    i18n_description="schema_login_customization_bg_overlay_desc",
                ),
                FieldSchema(
                    key="design_background_color",
                    label="Design background color",
                    description="Set the background color for split, centered card, and glass designs.",
                    type="string",
                    input_type="color",
                    placeholder="#ffffff",
                    max_length=7,
                    dependency="login_design",
                    dependency_value=["split", "split_image", "centered", "glass"],
                    i18n_label="schema_login_customization_design_bg_color",
                    i18n_description="schema_login_customization_design_bg_color_desc",
                ),
            ],
        ),
        Section(
            title="Branding",
            description="Manage how the login screen reflects your brand.",
            i18n_title="schema_login_customization_sec0_title",
            i18n_description="schema_login_customization_sec0_desc",
            fields=[
                FieldSchema(
                    key="show_custom_logo",
                    label="Show custom logo",
                    description="Display a custom logo on the login page if uploaded.",
                    type="boolean",
                    i18n_label="schema_login_customization_show_custom_logo",
                    i18n_description="schema_login_customization_show_custom_logo_desc",
                ),
                FieldSchema(
                    key="custom_logo_height",
                    label="Custom logo height",
                    description="Choose the display size for the custom logo.",
                    type="select",
                    options=[
                        {
                            "value": "small",
                            "label": "Small",
                            "i18n_label": "schema_option_size_small",
                        },
                        {
                            "value": "medium",
                            "label": "Medium",
                            "i18n_label": "schema_option_size_medium",
                        },
                        {
                            "value": "big",
                            "label": "Large",
                            "i18n_label": "schema_option_size_large",
                        },
                    ],
                    i18n_label="schema_login_customization_custom_logo_height",
                    i18n_description="schema_login_customization_custom_logo_height_desc",
                    dependency="show_custom_logo",
                    dependency_value=True,
                ),
            ],
        ),
    ],
)
