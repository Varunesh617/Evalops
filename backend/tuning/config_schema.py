"""JSON Schema generation for tuning UI components."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# UI hint models
# ---------------------------------------------------------------------------

class UISchema(BaseModel):
    """UI component hint for a configurable field."""

    component: str = "input"  # input, slider, dropdown, toggle, range, color
    label: str = ""
    help_text: str = ""
    placeholder: str = ""
    options: list[dict[str, Any]] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    group: str = "general"


class FieldSchema(BaseModel):
    """Full schema for a single configurable field."""

    name: str
    type: str
    default: Any = None
    description: str = ""
    required: bool = False
    validation: dict[str, Any] = Field(default_factory=dict)
    ui: UISchema = Field(default_factory=UISchema)


class SectionSchema(BaseModel):
    """Schema for a group of related fields."""

    name: str
    title: str
    description: str = ""
    fields: list[FieldSchema] = Field(default_factory=list)
    collapsible: bool = True


# ---------------------------------------------------------------------------
# Schema generator
# ---------------------------------------------------------------------------

class ConfigSchemaGenerator:
    """Generate JSON Schema and UI hints for all tuning components."""

    def generate_metric_schemas(self) -> SectionSchema:
        """Schema for metric selection and weighting."""
        return SectionSchema(
            name="metrics",
            title="Evaluation Metrics",
            description="Select and weight the metrics used to evaluate pipeline quality.",
            fields=[
                FieldSchema(
                    name="enabled_metrics",
                    type="array",
                    default=["faithfulness", "context_relevance"],
                    description="Metrics to include in evaluation.",
                    validation={"min_items": 1},
                    ui=UISchema(
                        component="toggle",
                        label="Enable Metrics",
                        help_text="Toggle individual metrics on or off.",
                        group="metrics",
                    ),
                ),
                FieldSchema(
                    name="metric_weights",
                    type="object",
                    default={"faithfulness": 1.0, "context_relevance": 1.0},
                    description="Weight multiplier for each metric (0.0–10.0).",
                    validation={"min": 0.0, "max": 10.0},
                    ui=UISchema(
                        component="slider",
                        label="Metric Weights",
                        help_text="Higher weight = more influence on composite score.",
                        min=0.0,
                        max=10.0,
                        step=0.1,
                        group="metrics",
                    ),
                ),
            ],
        )

    def generate_filter_schemas(self) -> SectionSchema:
        """Schema for guardrail filter configuration."""
        filter_names = [
            "prompt_injection", "pii", "toxicity",
            "faithfulness_check", "citation_validator",
        ]
        fields: list[FieldSchema] = []

        for fname in filter_names:
            fields.append(FieldSchema(
                name=f"filter_{fname}_enabled",
                type="boolean",
                default=True,
                description=f"Enable the {fname.replace('_', ' ')} filter.",
                ui=UISchema(
                    component="toggle",
                    label=f"{fname.replace('_', ' ').title()} Filter",
                    help_text=f"Toggle the {fname.replace('_', ' ')} guardrail filter.",
                    group="filters",
                ),
            ))
            fields.append(FieldSchema(
                name=f"filter_{fname}_threshold",
                type="number",
                default=0.5,
                description=f"Block threshold for {fname.replace('_', ' ')} (0.0–1.0).",
                validation={"min": 0.0, "max": 1.0},
                ui=UISchema(
                    component="slider",
                    label=f"{fname.replace('_', ' ').title()} Threshold",
                    help_text="Scores at or above this threshold trigger a block.",
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    unit="score",
                    group="filters",
                ),
            ))

        fields.append(FieldSchema(
            name="filter_priority",
            type="object",
            default={fname: i * 10 for i, fname in enumerate(filter_names)},
            description="Execution priority for each filter (higher = runs first).",
            ui=UISchema(
                component="dropdown",
                label="Filter Priority Order",
                help_text="Determines which filters run first.",
                group="filters",
            ),
        ))

        return SectionSchema(
            name="filters",
            title="Guardrail Filters",
            description="Configure which guardrails are active and their sensitivity thresholds.",
            fields=fields,
        )

    def generate_optimization_schemas(self) -> SectionSchema:
        """Schema for optimization goal configuration."""
        return SectionSchema(
            name="optimization",
            title="Optimization Goals",
            description="Define what the optimizer should prioritize and its constraints.",
            fields=[
                FieldSchema(
                    name="objective",
                    type="string",
                    default="balanced",
                    description="Primary optimization objective.",
                    ui=UISchema(
                        component="dropdown",
                        label="Optimization Objective",
                        help_text="Choose what to optimize for.",
                        options=[
                            {"value": "quality", "label": "Quality — maximize accuracy"},
                            {"value": "cost", "label": "Cost — minimize spend"},
                            {"value": "latency", "label": "Latency — minimize response time"},
                            {"value": "balanced", "label": "Balanced — trade off all three"},
                        ],
                        group="optimization",
                    ),
                ),
                FieldSchema(
                    name="max_cost_usd",
                    type="number",
                    default=None,
                    description="Maximum allowed cost per run in USD.",
                    validation={"min": 0.0},
                    ui=UISchema(
                        component="slider",
                        label="Max Cost ($)",
                        help_text="Hard budget cap. Null = unlimited.",
                        min=0.0,
                        max=20.0,
                        step=0.1,
                        unit="USD",
                        group="optimization",
                    ),
                ),
                FieldSchema(
                    name="min_quality",
                    type="number",
                    default=None,
                    description="Minimum acceptable quality score (0.0–1.0).",
                    validation={"min": 0.0, "max": 1.0},
                    ui=UISchema(
                        component="slider",
                        label="Min Quality",
                        help_text="Optimizer will reject solutions below this threshold.",
                        min=0.0,
                        max=1.0,
                        step=0.01,
                        group="optimization",
                    ),
                ),
                FieldSchema(
                    name="max_latency_ms",
                    type="number",
                    default=None,
                    description="Maximum allowed latency in milliseconds.",
                    validation={"min": 0.0},
                    ui=UISchema(
                        component="slider",
                        label="Max Latency (ms)",
                        help_text="Hard latency cap. Null = unlimited.",
                        min=0.0,
                        max=30000.0,
                        step=100.0,
                        unit="ms",
                        group="optimization",
                    ),
                ),
                FieldSchema(
                    name="max_trials",
                    type="integer",
                    default=50,
                    description="Maximum number of optimization trials.",
                    validation={"min": 1, "max": 500},
                    ui=UISchema(
                        component="slider",
                        label="Max Trials",
                        help_text="More trials = better results but higher cost.",
                        min=1,
                        max=500,
                        step=1,
                        group="optimization",
                    ),
                ),
                FieldSchema(
                    name="max_duration_seconds",
                    type="number",
                    default=3600.0,
                    description="Maximum wall-clock time for the optimization run.",
                    validation={"min": 60.0, "max": 86400.0},
                    ui=UISchema(
                        component="slider",
                        label="Max Duration",
                        help_text="Optimization stops after this time.",
                        min=60.0,
                        max=86400.0,
                        step=60.0,
                        unit="seconds",
                        group="optimization",
                    ),
                ),
            ],
        )

    def generate_preset_schemas(self) -> SectionSchema:
        """Schema for preset management."""
        return SectionSchema(
            name="presets",
            title="Tuning Presets",
            description="Apply pre-configured settings or save your own.",
            fields=[
                FieldSchema(
                    name="selected_preset",
                    type="string",
                    default=None,
                    description="ID of the preset to apply.",
                    ui=UISchema(
                        component="dropdown",
                        label="Apply Preset",
                        help_text="Select a built-in or custom preset to load its configuration.",
                        options=[
                            {"value": "preset-healthcare", "label": "Healthcare — HIPAA strict"},
                            {"value": "preset-startup", "label": "Startup — fast iteration"},
                            {"value": "preset-enterprise", "label": "Enterprise — compliance"},
                            {"value": "preset-research", "label": "Research — max quality"},
                        ],
                        group="presets",
                    ),
                ),
                FieldSchema(
                    name="save_as_preset",
                    type="boolean",
                    default=False,
                    description="Save current configuration as a new preset.",
                    ui=UISchema(
                        component="toggle",
                        label="Save as Preset",
                        help_text="Store current settings for reuse.",
                        group="presets",
                    ),
                ),
                FieldSchema(
                    name="preset_name",
                    type="string",
                    default="",
                    description="Name for the new custom preset.",
                    ui=UISchema(
                        component="input",
                        label="Preset Name",
                        placeholder="My Custom Preset",
                        group="presets",
                    ),
                ),
            ],
        )

    def generate_all_schemas(self) -> dict[str, SectionSchema]:
        """Generate schemas for all tuning sections."""
        return {
            "metrics": self.generate_metric_schemas(),
            "filters": self.generate_filter_schemas(),
            "optimization": self.generate_optimization_schemas(),
            "presets": self.generate_preset_schemas(),
        }

    def to_json_schema(self) -> dict[str, Any]:
        """Convert all schemas to a single JSON Schema object for UI rendering."""
        sections = self.generate_all_schemas()
        properties: dict[str, Any] = {}
        for name, section in sections.items():
            section_props: dict[str, Any] = {}
            for field in section.fields:
                section_props[field.name] = {
                    "type": field.type,
                    "default": field.default,
                    "description": field.description,
                }
                if field.validation:
                    section_props[field.name].update(field.validation)
                section_props[field.name]["x-ui"] = field.ui.model_dump()
            properties[name] = {
                "type": "object",
                "title": section.title,
                "description": section.description,
                "properties": section_props,
                "x-collapsible": section.collapsible,
            }

        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "EvalOps Tuning Configuration",
            "description": "Complete schema for the EvalOps tuning interface.",
            "type": "object",
            "properties": properties,
        }
