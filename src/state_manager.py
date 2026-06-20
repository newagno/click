import os
import json
from typing import Dict, Tuple

class LocalStateManager:
    """Manages bot state using a local state.json file committed to the repository."""
    def __init__(self, state_file: str = "state.json"):
        # The state file will be located at the root of the project
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        self.state_file = os.path.join(root_dir, state_file)
        
    def load(self) -> Tuple[Dict, str]:
        """Returns (state, source). source is always 'local' or 'default'."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    return state, "local"
            except Exception as e:
                print(f"Failed to load local state: {e}")
                
        return {"last_claim": None, "streak": 0}, "default"
    
    def save(self, state: Dict) -> bool:
        """Returns True if successfully saved to local file."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            return True
        except Exception as e:
            print(f"Failed to save local state: {e}")
            return False
