import json
import time
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional
from .linear_sync import LinearSync
from attractor_parser import AttractorParser
from .engine import PipelineEngine
from .model_resolver import ModelResolver
from .handlers import CodergenHandler, HumanWaitHandler, ConditionalHandler

class LinearFactoryWatcher:
    \"\"\"Watchdog that polls Linear for issues assigned to the AI Factory with dependency awareness.\"\"\"
    
    def __init__(self, api_key: str, team_id: str, launderette_client: Any):
        self.sync = LinearSync(api_key, team_id)
        self.launderette = launderette_client
        self.parser = AttractorParser()
        self.resolver = ModelResolver()
        self.templates_dir = Path(\"~/.hermes/skills/ai-factory/templates\").expanduser()

    def _get_blocking_issues(self, issue_id: str) -> List[str]:
        query = f\"\"\"
        query {{
            issue(id: \"{issue_id}\") {{
                description
            }}
        }}
        \"\"\"
        try:
            response = requests.post(self.sync.url, headers=self.sync.headers, json={\"query\": query})
            data = response.json()
            desc = data[\"data\"][\"issue\"][\"description\"] or \"\"
            import re
            matches = re.findall(r'depends on:\s*([A-Z]+-\d+)', desc, re.IGNORECASE)
            return matches
        except Exception as e:
            print(f\"Dependency check error: {e}\")
            return []

    def _is_issue_done(self, issue_id: str) -> bool:
        query = f\"\"\"
        query {{
            issue(id: \"{issue_id}\") {{
                state {{ name }}
            }}
        }}
        \"\"\"
        try:
            response = requests.post(self.sync.url, headers=self.sync.headers, json={\"query\": query})
            data = response.json()
            state_name = data[\"data\"][\"issue\"][\"state\"][\"name\"]
            return state_name.lower() == \"done\"
        except Exception:
            return False

    def poll(self):
        \"\"\"Finds issues tagged 'ai-factory' and respects dependencies.\"\"\"
        # We now pull ALL issues for the team and filter locally for maximum reliability
        query = \"\"\"
        query {
            issues(filter: { team: { id: { eq: \"%s\" } } }) {
                nodes {
                    id
                    title
                    description
                    state { name }
                    labels { nodes { name } }
                }
            }
        }
        \"\"\" % (self.sync.team_id)
        
        try:
            response = requests.post(self.sync.url, headers=self.sync.headers, json={\"query\": query})
            issues = response.json().get(\"data\", {}).get(\"issues\", {}).get(\"nodes\", [])
            
            for issue in issues:
                # Filter locally for label 'ai-factory' and state not 'Done'
                labels = [l[\"name\"] for l in issue[\"labels\"][\"nodes\"]]
                state = issue[\"state\"][\"name\"].lower() if issue[\"state\"] else \"\"
                
                if \"ai-factory\" in labels and state != \"done\":
                    issue_id = issue[\"id\"]
                    desc = issue[\"description\"] or \"\"
                    
                    # Check for dependencies
                    dependencies = self._get_blocking_issues(issue_id)
                    blocked = False
                    for dep in dependencies:
                        if not self._is_issue_done(dep):
                            print(f\"Issue {issue_id} is blocked by {dep}. Skipping.\")
                            blocked = True
                            break
                    
                    if not blocked:
                        self.process_issue(issue_id, desc)
                
        except Exception as e:
            print(f\"Polling error: {e}\")

    def process_issue(self, issue_id: str, description: str):
        spec_name = \"feature-impl\" 
        if \"spec:\" in description.lower():
            try:
                spec_name = description.lower().split(\"spec:\")[e.g., 1].split()[0].strip()
            except: pass

        spec_path = self.templates_dir / f\"{spec_name}.dot\"
        if not spec_path.exists():
            self.sync.post_event(issue_id, f\"❌ Factory Error: Spec `{spec_name}` not found.\")
            return

        handlers = {
            \"codergen\": CodergenHandler(self.launderette, self.resolver),
            \"wait_human\": HumanWaitHandler(self.launderette),
            \"conditional\": ConditionalHandler()
        }

        graph = self.parser.parse(spec_path.read_text())
        run_id = f\"run_{int(time.time())}_{issue_id}\"
        engine = PipelineEngine(graph, run_id, Path(\"~/.hermes/ai-factory/runs\").expanduser(), self.launderette)
        
        self.sync.post_event(issue_id, f\"🚀 Factory starting pipeline: `{spec_name}`. Run ID: {run_id}\")
        result = engine.run(handlers)
        
        status_msg = f\"✅ Pipeline completed with status: {result.status}. Notes: {result.notes}\"
        self.sync.post_event(issue_id, status_msg)
        self.sync.update_issue_state(issue_id, \"Done\")
