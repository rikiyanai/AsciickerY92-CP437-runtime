"""
engine.py -- Programmatic wizard engine for asset generation.

This module extracts the wizard logic from cli.py into a state machine
 that can be driven by either a CLI (questionary) or an MCP server.

SUMMARY->DONE transition POSTs nav state to pipeline-v3 /pipeline/run (RQ-022 / FL-1186).
"""
import json
import os
import urllib.error
import urllib.request
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, field

from .navigation import WizardNav, WizardScreen
from .intents import VALID_INTENTS, get_intent_display_name, is_template_required
from .availability import check_pipeline_availability
from .validation import validate_template_loadable

PIPELINE_SERVER_URL = os.environ.get("PIPELINE_SERVER_URL", "http://localhost:8090")

@dataclass
class WizardStep:
    """Metadata for a single wizard step/screen."""
    id: WizardScreen
    title: str
    prompt: str
    choices: List[Dict[str, Any]] = field(default_factory=list)
    type: str = "select"  # "select" or "text"
    default: Optional[Any] = None
    back_available: bool = True

class WizardEngine:
    """State machine for the asset generation wizard."""

    def __init__(self):
        self.nav = WizardNav()
        self.availability = check_pipeline_availability()
        self.current_screen = WizardScreen.INTENT

    def get_current_step(self) -> WizardStep:
        """Get metadata for the current step."""
        screen = self.current_screen
        
        if screen == WizardScreen.INTENT:
            from .availability import format_intent_choice
            choices = [
                format_intent_choice('new_character', 'New character asset', self.availability),
                format_intent_choice('convert_sheet', 'Convert sprite sheet to XP format', self.availability),
                format_intent_choice('render_blender', 'Render from Blender scene', self.availability),
                format_intent_choice('import_mesh', 'Import 3D model (OBJ/STL/FBX/GLTF)', self.availability),
                format_intent_choice('modify_xp', 'Modify existing XP asset', self.availability),
            ]
            return WizardStep(
                id=WizardScreen.INTENT,
                title="Intent Selection",
                prompt="What do you want to create?",
                choices=choices,
                back_available=False
            )

        elif screen == WizardScreen.ASSET_TYPE:
            return WizardStep(
                id=WizardScreen.ASSET_TYPE,
                title="Asset Type",
                prompt="What type of asset?",
                choices=[
                    {"name": "Character (animated sprite)", "value": "character"},
                    {"name": "Item (static object)", "value": "item"},
                    {"name": "UI element (interface graphics)", "value": "ui"},
                    {"name": "Custom (raw mode)", "value": "custom"},
                ]
            )

        elif screen == WizardScreen.TEMPLATE:
            asset_type = self.nav.get_state('asset_type')
            templates = self._get_templates_by_type(asset_type)
            choices = []
            for t in templates:
                choices.append({
                    "name": f"{t['name']} - {t['description']}",
                    "value": t["file"],
                })
            choices.append({"name": "Custom (raw mode)", "value": "__custom__"})
            
            return WizardStep(
                id=WizardScreen.TEMPLATE,
                title="Template Selection",
                prompt=f"Select a {asset_type} template:",
                choices=choices
            )

        elif screen == WizardScreen.SOURCE:
            return WizardStep(
                id=WizardScreen.SOURCE,
                title="Source Selection",
                prompt="Source type:",
                choices=[
                    {"name": "File (PNG sprite sheet)", "value": "file"},
                    {"name": "AI (PNG with magenta transparency)", "value": "ai"},
                    {"name": "Blender (render from scene)", "value": "blender"},
                    {"name": "3D Mesh (OBJ/STL/FBX/GLTF/GLB/PLY)", "value": "mesh"},
                    {"name": "AI Batch (generate with AI + prompt pack)", "value": "ai_batch"},
                ]
            )

        elif screen == WizardScreen.AI_CONFIG:
            # Two-step screen: first provider, then prompt pack path
            if self.nav.get_state('ai_provider') is None:
                return WizardStep(
                    id=WizardScreen.AI_CONFIG,
                    title="AI Configuration",
                    prompt="AI provider:",
                    type="select",
                    choices=[
                        {"name": "Stub (test output)", "value": "stub"},
                        {"name": "Gemini (requires API key)", "value": "gemini"},
                    ]
                )
            else:
                return WizardStep(
                    id=WizardScreen.AI_CONFIG,
                    title="AI Configuration",
                    prompt="Prompt pack path:",
                    type="text"
                )

        elif screen == WizardScreen.INPUT_PATH:
            source_type = self.nav.get_state('source_type')
            prompt = "Input path:"
            if source_type == "blender":
                prompt = "Blender .blend file path:"
            elif source_type == "mesh":
                prompt = "3D model file path (.obj, .stl, .fbx, .gltf, .glb, .ply):"
            elif source_type == "ai_batch":
                prompt = "Manifest JSON path:"

            return WizardStep(
                id=WizardScreen.INPUT_PATH,
                title="Input Path",
                prompt=prompt,
                type="text"
            )

        elif screen == WizardScreen.SUMMARY:
            state = self.nav.state
            summary_text = f"Intent: {state.get('intent')}\nType: {state.get('asset_type')}\nSource: {state.get('source_type')}\nPath: {state.get('input_path')}"
            return WizardStep(
                id=WizardScreen.SUMMARY,
                title="Summary",
                prompt=f"Summary of choices:\n{summary_text}\n\nProceed?",
                choices=[
                    {"name": "Yes, proceed", "value": "proceed"},
                    {"name": "Cancel", "value": "cancel"},
                ]
            )
        
        return WizardStep(id=screen, title=str(screen), prompt="Continue?")

    def _get_templates_by_type(self, asset_type: str) -> List[Dict[str, Any]]:
        """Find templates matching the given asset type."""
        # templates lives in scripts/pipeline/templates
        # this file is scripts/pipeline/wizard/engine.py
        templates_dir = Path(__file__).parent.parent / "templates"
        templates = []
        
        if not templates_dir.exists():
            return templates

        for tf in templates_dir.glob("*.json"):
            try:
                # Load metadata only
                import json
                with open(tf, 'r') as f:
                    data = json.load(f)
                
                if data.get("type") == asset_type:
                    templates.append({
                        "name": data.get("name", tf.stem),
                        "description": data.get("description", ""),
                        "file": str(tf)
                    })
            except Exception:
                continue
        return templates

    def submit_answer(self, answer: Any) -> Tuple[WizardScreen, Dict[str, Any]]:
        """Submit an answer and advance the state machine."""
        if answer == "__BACK__":
            self.current_screen = self.nav.go_back() or WizardScreen.INTENT
            return self.current_screen, self.nav.state

        if self.current_screen == WizardScreen.INTENT:
            self.nav.set_state('intent', answer)
            self.nav.push_screen(WizardScreen.INTENT)
            self.current_screen = WizardScreen.ASSET_TYPE
            
        elif self.current_screen == WizardScreen.ASSET_TYPE:
            self.nav.set_state('asset_type', answer)
            self.nav.push_screen(WizardScreen.ASSET_TYPE)
            if answer == "custom":
                self.nav.set_state('is_custom_mode', True)
                self.current_screen = WizardScreen.SOURCE
            else:
                self.current_screen = WizardScreen.TEMPLATE
                
        elif self.current_screen == WizardScreen.TEMPLATE:
            if answer == "__custom__":
                self.nav.set_state('is_custom_mode', True)
                self.nav.set_state('template', None)
            else:
                from scripts.pipeline.templates.loader import TemplateLoader
                template = TemplateLoader.from_file(Path(answer))
                self.nav.set_state('template', template)
            
            self.nav.push_screen(WizardScreen.TEMPLATE)
            self.current_screen = WizardScreen.SOURCE

        elif self.current_screen == WizardScreen.SOURCE:
            self.nav.set_state('source_type', answer)
            self.nav.push_screen(WizardScreen.SOURCE)
            if answer == "ai_batch":
                self.current_screen = WizardScreen.AI_CONFIG
            else:
                self.current_screen = WizardScreen.INPUT_PATH

        elif self.current_screen == WizardScreen.AI_CONFIG:
            if self.nav.get_state('ai_provider') is None:
                # First step: provider selection
                self.nav.set_state('ai_provider', answer)
                # Stay on AI_CONFIG for prompt pack path (second step)
                # Don't push to history — same screen, second step
            else:
                # Second step: prompt pack path
                self.nav.set_state('ai_prompt_pack_path', answer)
                self.nav.push_screen(WizardScreen.AI_CONFIG)
                self.current_screen = WizardScreen.INPUT_PATH

        elif self.current_screen == WizardScreen.INPUT_PATH:
            self.nav.set_state('input_path', answer)
            self.nav.push_screen(WizardScreen.INPUT_PATH)
            self.current_screen = WizardScreen.SUMMARY

        elif self.current_screen == WizardScreen.SUMMARY:
            if answer == "proceed":
                # POST nav state to pipeline-v3 /pipeline/run (RQ-022 / FL-1186)
                self._post_pipeline_run()
                self.current_screen = WizardScreen.DONE
            else:
                # Cancelled or something else
                pass

        return self.current_screen, self.nav.state

    def _post_pipeline_run(self) -> Optional[dict]:
        """POST the current wizard nav state to pipeline-v3 /pipeline/run.

        Returns the server response dict on success, or None on failure.
        The result is stored in nav state as 'pipeline_run_result'.
        """
        state = self.nav.state
        template = state.get('template')

        run_payload: Dict[str, Any] = {
            "name": state.get('name') or (getattr(template, 'name', None) if template else None) or "wizard_asset",
            "asset_type": state.get('asset_type') or "custom",
            "source_type": state.get('source_type') or "file",
            "source_path": str(state.get('input_path')) if state.get('input_path') else None,
            "intent": state.get('intent'),
        }

        if template:
            run_payload["template"] = getattr(template, 'name', str(template))

        if state.get('source_type') == 'ai_batch':
            run_payload["ai_provider"] = state.get('ai_provider', 'stub')
            run_payload["ai_prompt_pack_path"] = state.get('ai_prompt_pack_path')

        try:
            url = f"{PIPELINE_SERVER_URL}/pipeline/run"
            data = json.dumps(run_payload).encode()
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())
            self.nav.set_state('pipeline_run_result', result)
            return result
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error_result = {"success": False, "error": str(exc)}
            self.nav.set_state('pipeline_run_result', error_result)
            return None
