import json
import requests
from pathlib import Path
from typing import Any, Dict, Optional, List

class LinearSync:
    """Syncs AI Factory pipeline events with Linear issues."""
    
    def __init__(self, api_key: str, team_id: str):
        self.api_key = api_key
        self.team_id = team_id
        self.url = "https://api.linear.app/graphql"
        self.headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json"
        }

    def post_event(self, issue_id: str, message: str):
        """Posts a comment to a Linear issue documenting a pipeline event."""
        query = """
        mutation CreateComment($issueId: String!, $body: String!) {
            createComment(input: {issueId: $issueId, body: $body}) {
                success
            }
        }
        """
        variables = {"issueId": issue_id, "body": message}
        try:
            response = requests.post(self.url, headers=self.headers, json={"query": query, "variables": variables})
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def update_issue_state(self, issue_id: str, state_name: str):
        """Updates the state of the Linear issue (e.g., 'In Progress', 'Awaiting Review')."""
        # First resolve state name to state ID
        state_id = self._get_state_id(state_name)
        if not state_id:
            return {"error": f"State {state_name} not found"}

        query = """
        mutation UpdateIssue($id: String!, $stateId: String!) {
            updateIssue(input: {id: $id, stateId: $stateId}) {
                success
            }
        }
        """
        variables = {"id": issue_id, "stateId": state_id}
        try:
            response = requests.post(self.url, headers=self.headers, json={"query": query, "variables": variables})
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def _get_state_id(self, state_name: str) -> Optional[str]:
        query = """
        query {
            issueStates {
                nodes {
                    name
                    id
                }
            }
        }
        """
        try:
            response = requests.post(self.url, headers=self.headers, json={"query": query})
            data = response.json()
            for state in data["data"]["issueStates"]["nodes"]:
                if state["name"].lower() == state_name.lower():
                    return state["id"]
        except Exception:
            pass
        return None
