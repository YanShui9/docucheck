"""Authorize every /api/v1 route after FastAPI resolves its path parameters."""

from fastapi import Request
from fastapi.routing import APIRoute


class AuthorizedRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def authorized(request: Request):
            authenticator = request.app.state.authenticator
            if not authenticator.enabled:
                return await original(request)
            principal = authenticator.authenticate(
                request.headers.get("authorization"),
            )
            request.state.principal = principal
            workspace = request.app.state.workspace
            params = request.path_params
            action = "read" if request.method in {"GET", "HEAD"} else "write"
            if request.method == "DELETE":
                action = "owner"
            if "case_id" in params:
                workspace.require_case(params["case_id"], principal, action)
            elif "conversation_id" in params:
                workspace.require_case(workspace.case_for_conversation(
                    params["conversation_id"],
                ), principal, action)
            elif "run_id" in params:
                workspace.require_case(workspace.case_for_run(
                    params["run_id"],
                ), principal, action)
            elif "project_id" in params:
                project_action = ("owner" if request.url.path.endswith("/members")
                                  and request.method == "POST" else action)
                workspace.require_project(params["project_id"], principal,
                                          project_action)
            return await original(request)

        return authorized
