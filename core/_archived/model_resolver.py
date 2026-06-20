import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

class ModelResolver:
    """Resolves logical model classes to specific model/provider chains.
    
    This leverages the Hermes Agent's native fallback mechanism by defining
    a priority list of models for each logical class.
    """
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path("~/.hermes/ai-factory-models.json").expanduser()
        self.mapping = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        
        # Default Mapping: Mapping logic to the high/med/low tiers discussed
        return {
            "classes": {
                ".frontier": {
                    "primary": "claude-3-opus",
                    "fallback": "gpt-4o",
                    "provider_hint": "anthropic"
                },
                ".reasoning": {
                    "primary": "deepseek-r1",
                    "fallback": "claude-3-5-sonnet",
                    "provider_hint": "ollama"
                },
                ".fast": {
                    "primary": "llama3",
                    "fallback": "gpt-4o-mini",
                    "provider_hint": "ollama"
                },
                ".default": {
                    "primary": "claude-3-5-sonnet",
                    "fallback": "gpt-4o",
                    "provider_hint": "anthropic"
                }
            }
        }

    def resolve(self, node_class: str) -> Dict[str, str]:
        """Returns the model chain for a given node class."""
        # Ensure class starts with dot
        cls = node_class if node_class.startswith(".") else f".{node_class}"
        
        # Find match or fallback to default
        config = self.mapping["classes"].get(cls, self.mapping["classes"][".default"])
        
        return {
            "model": config["primary"],
            "fallback": config.get("fallback"),
            "provider": config.get("provider_hint")
        }
