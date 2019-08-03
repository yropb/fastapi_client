from contextlib import suppress
from typing import Optional

from fastapi.openapi.models import OAuthFlowPassword
from httpx import AsyncRequest, AsyncResponse
from pydantic import BaseModel
from starlette.status import HTTP_401_UNAUTHORIZED

from fastapi_client.api_client import Send
from fastapi_client.exceptions import UnexpectedResponse
from fastapi_client.password_flow_client import (
    AccessTokenRequest,
    PasswordFlowClient,
    RefreshTokenRequest,
    TokenSuccessResponse,
)


class AuthState(BaseModel):
    username: Optional[str]
    password: Optional[str]
    access_token: Optional[str]
    refresh_token: Optional[str]
    scope: Optional[str]

    def access_token_request(self) -> Optional[AccessTokenRequest]:
        if self.username is None or self.password is None:
            return None
        return AccessTokenRequest(username=self.username, password=self.password, scope=self.scope)

    def refresh_token_request(self) -> Optional[RefreshTokenRequest]:
        if self.refresh_token is None:
            return None
        return RefreshTokenRequest(refresh_token=self.refresh_token, scope=self.scope)

    def update(self, token_success_response: TokenSuccessResponse) -> None:
        self.access_token = token_success_response.access_token
        self.refresh_token = token_success_response.refresh_token
        self.scope = token_success_response.scope


class AuthMiddleware:
    def __init__(self, auth_state: AuthState, flow: OAuthFlowPassword) -> None:
        self.auth_state = auth_state
        self.flow_client = PasswordFlowClient(flow)

    @staticmethod
    def set_access_header(token: str, request: AsyncRequest, *, replace: bool) -> None:
        key = "authorization"
        value = f"bearer {token}"
        if replace:
            request.headers[key] = value
        else:
            request.headers.setdefault(key, value)

    async def login(self) -> Optional[TokenSuccessResponse]:
        access_token_request = self.auth_state.access_token_request()
        if access_token_request is None:
            return None
        with suppress(UnexpectedResponse):
            token_response = await self.flow_client.request_access_token(access_token_request)
            if isinstance(token_response, TokenSuccessResponse):
                self.auth_state.update(token_response)
                return token_response
        return None

    async def refresh(self) -> Optional[TokenSuccessResponse]:
        refresh_token_request = self.auth_state.refresh_token_request()
        if refresh_token_request is None:
            return None
        with suppress(UnexpectedResponse):
            token_response = await self.flow_client.request_refresh_token(refresh_token_request)
            if isinstance(token_response, TokenSuccessResponse):
                self.auth_state.update(token_response)
                return token_response
        return None

    async def __call__(self, request: AsyncRequest, call_next: Send) -> AsyncResponse:
        access_token = self.auth_state.access_token
        if access_token is not None:
            self.set_access_header(access_token, request, replace=False)
        response = await call_next(request)
        if response.status_code != HTTP_401_UNAUTHORIZED:
            return response
        tokens = await self.refresh()
        if tokens is None:
            tokens = await self.login()
        if tokens:
            self.set_access_header(tokens.access_token, request, replace=True)
            return await call_next(request)  # note: won't work with streaming input
        return response
