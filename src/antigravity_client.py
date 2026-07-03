#!/usr/bin/env python3
"""
Antigravity/OpenCode API Client

Uses Antigravity's OAuth credentials to make calls to the Code Assist API.
This uses the same quota as OpenCode with Antigravity provider.

Account: starlinkchacrapoli@gmail.com (configured in OpenCode)

Usage:
    from antigravity_client import AntigravityClient
    
    client = AntigravityClient()
    response = client.generate_content("¿Cuál es la capital de Francia?")
    print(response)

Or from command line:
    python3 antigravity_client.py "Tu prompt aquí"
    python3 antigravity_client.py --model gemini-3-flash-preview "Tu prompt"
    python3 antigravity_client.py --system "Eres un experto en Python" "Explica decoradores"
"""

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import requests

# Antigravity/OpenCode OAuth credentials (from opencode-antigravity-auth plugin)
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")

# API endpoints
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# Use daily sandbox endpoint - has Claude models available (prod only has Gemini)
CODE_ASSIST_ENDPOINT = "https://daily-cloudcode-pa.sandbox.googleapis.com"
CODE_ASSIST_API_VERSION = "v1internal"

# Fallback endpoints in case daily is down
CODE_ASSIST_ENDPOINT_FALLBACKS = [
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://autopush-cloudcode-pa.sandbox.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
]

# Paths - OpenCode stores accounts in ~/.config/opencode/ or ~/.local/share/opencode/
# Also check /app/antigravity-accounts.json for Docker containers
OPENCODE_ACCOUNTS_PATHS = [
    Path("/app/antigravity-accounts.json"),  # Docker container (mounted volume)
    Path.home() / ".config" / "opencode" / "antigravity-accounts.json",
    Path.home() / ".local" / "share" / "opencode" / "antigravity-accounts.json",
]
CACHE_PATH = Path.home() / ".cache" / "antigravity-client" / "cache.json"

# Account mapping for specific models (model_id -> account_index)
# Account indices: 0=cristiancatanza, 1=catanzarocristiane, 2=starlinkchacrapoli, 3=catagptcodex
MODEL_ACCOUNT_MAPPING = {
    "tab_flash_lite_preview": 3,  # catagptcodex@gmail.com
    "tab_jump_flash_lite_preview": 3,  # catagptcodex@gmail.com (mismo modelo, variante jump)
}

# Available models - nombres reales que acepta la API de Code Assist
MODELS = {
    # Gemini 3 models (Antigravity quota via daily sandbox endpoint)
    "gemini-3-flash": "Gemini 3 Flash (default)",
    "gemini-3-pro": "Gemini 3 Pro",
    # Gemini 2.5 models (Gemini CLI quota)
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    # Gemini 3 preview models (Gemini CLI quota)
    "gemini-3-flash-preview": "Gemini 3 Flash Preview",
    "gemini-3-pro-preview": "Gemini 3 Pro Preview",
    # Experimental/preview models
    "tab_flash_lite_preview": "Tab Flash Lite Preview (experimental)",
    # Claude models (Antigravity quota via daily sandbox endpoint)
    "claude-sonnet-4-5-thinking": "Claude Sonnet 4.5 Thinking",
    "claude-opus-4-5-thinking": "Claude Opus 4.5 Thinking",
}

DEFAULT_MODEL = "gemini-3-flash"

# Thinking budgets for Claude models
CLAUDE_THINKING_BUDGETS = {
    "low": 8192,
    "medium": 16384,
    "high": 32768,
}
CLAUDE_THINKING_MAX_OUTPUT_TOKENS = 64000

def _parse_variant(model: str) -> Optional[str]:
    if "/" in model:
        _, variant = model.split("/", 1)
        return variant
    return None


def _strip_variant(model: str) -> str:
    return model.split("/", 1)[0]


def _resolve_alias(model: str) -> str:
    normalized = model.lower()
    aliases = {
        "gemini-3-pro-low": "gemini-3-pro/low",
        "gemini-3-pro-high": "gemini-3-pro/high",
        "gemini-3-flash-minimal": "gemini-3-flash/minimal",
        "gemini-3-flash-low": "gemini-3-flash/low",
        "gemini-3-flash-medium": "gemini-3-flash/medium",
        "gemini-3-flash-high": "gemini-3-flash/high",
        "claude-sonnet-4-5-thinking-low": "claude-sonnet-4-5-thinking/low",
        "claude-sonnet-4-5-thinking-medium": "claude-sonnet-4-5-thinking/medium",
        "claude-sonnet-4-5-thinking-high": "claude-sonnet-4-5-thinking/high",
        "claude-opus-4-5-thinking-low": "claude-opus-4-5-thinking/low",
        "claude-opus-4-5-thinking-medium": "claude-opus-4-5-thinking/medium",
        "claude-opus-4-5-thinking-high": "claude-opus-4-5-thinking/high",
        "antigravity-gemini-3-pro-low": "gemini-3-pro/low",
        "antigravity-gemini-3-pro-high": "gemini-3-pro/high",
        "antigravity-gemini-3-pro/low": "gemini-3-pro/low",
        "antigravity-gemini-3-pro/high": "gemini-3-pro/high",
        "antigravity-gemini-3-pro": "gemini-3-pro",
        "gemini-3.1-pro-low": "gemini-3.1-pro/low",
        "gemini-3.1-pro-high": "gemini-3.1-pro/high",
        "antigravity-gemini-3.1-pro-low": "gemini-3.1-pro/low",
        "antigravity-gemini-3.1-pro-high": "gemini-3.1-pro/high",
        "antigravity-gemini-3.1-pro/low": "gemini-3.1-pro/low",
        "antigravity-gemini-3.1-pro/high": "gemini-3.1-pro/high",
        "antigravity-gemini-3.1-pro": "gemini-3.1-pro",

        "antigravity-gemini-3-flash": "gemini-3-flash",
        "antigravity-gemini-3-flash/minimal": "gemini-3-flash/minimal",
        "antigravity-gemini-3-flash/low": "gemini-3-flash/low",
        "antigravity-gemini-3-flash/medium": "gemini-3-flash/medium",
        "antigravity-gemini-3-flash/high": "gemini-3-flash/high",
        "antigravity-claude-sonnet-4-5-thinking": "claude-sonnet-4-5-thinking",
        "antigravity-claude-sonnet-4-5-thinking/low": "claude-sonnet-4-5-thinking/low",
        "antigravity-claude-sonnet-4-5-thinking/medium": "claude-sonnet-4-5-thinking/medium",
        "antigravity-claude-sonnet-4-5-thinking/high": "claude-sonnet-4-5-thinking/high",
        "antigravity-claude-opus-4-5-thinking": "claude-opus-4-5-thinking",
        "antigravity-claude-opus-4-5-thinking/low": "claude-opus-4-5-thinking/low",
        "antigravity-claude-opus-4-5-thinking/medium": "claude-opus-4-5-thinking/medium",
        "antigravity-claude-opus-4-5-thinking/high": "claude-opus-4-5-thinking/high",
    }
    return aliases.get(normalized, model)

def _resolve_api_model_name(model: str) -> str:
    """
    Resolve the model name that should be sent in request JSON.
    Antigravity expects Gemini 3 Pro variants as model suffixes (-low/-high),
    while Flash and other families use base model + generationConfig thinking.
    """
    resolved = _resolve_alias(model)
    base_model = _strip_variant(resolved)
    variant = _parse_variant(resolved)
    model_lower = base_model.lower()

    if model_lower in ("gemini-3-pro", "gemini-3.1-pro"):
        level = variant if variant in {"low", "high"} else "low"
        return f"{base_model}-{level}"

    return base_model


def get_model_config(model: str) -> Dict[str, Any]:
    """
    Returns the appropriate generation config for each model type.
    Based on opencode-antigravity-auth plugin logic.
    """
    resolved = _resolve_alias(model)
    base_model = _strip_variant(resolved)
    variant = _parse_variant(resolved)
    model_lower = base_model.lower()
    config: Dict[str, Any] = {}

    # Gemini 3 Pro with thinking levels
    if model_lower in ("gemini-3-pro", "gemini-3.1-pro") and variant in {"low", "high"}:
        config["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingLevel": variant
        }
    # Gemini 3 Flash - thinking variants
    elif model_lower == "gemini-3-flash" and variant in {"minimal", "low", "medium", "high"}:
        config["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingLevel": variant
        }
    # Gemini 3 Flash - default low thinking if no variant provided
    elif model_lower == "gemini-3-flash":
        config["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingLevel": "low"
        }
    # Claude with thinking
    elif "claude" in model_lower and "thinking" in model_lower:
        thinking_level = variant or "high"
        budget = CLAUDE_THINKING_BUDGETS.get(thinking_level, CLAUDE_THINKING_BUDGETS["high"])
        config["thinkingConfig"] = {
            "include_thoughts": True,
            "thinking_budget": budget
        }
        config["maxOutputTokens"] = CLAUDE_THINKING_MAX_OUTPUT_TOKENS

    return config

def get_extra_headers(model: str) -> Dict[str, str]:
    """
    Returns extra headers needed for specific models.
    """
    resolved = _resolve_alias(model)
    model_lower = _strip_variant(resolved).lower()
    headers = {}

    # Claude thinking models need interleaved thinking header
    if "claude" in model_lower and "thinking" in model_lower:
        headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"

    return headers


def requires_streaming(model: str) -> bool:
    """
    Check if a model requires the streaming endpoint.
    The Code Assist API returns 429 for non-streaming requests with Claude and Gemini Pro models.
    """
    resolved = _resolve_alias(model)
    model_lower = _strip_variant(resolved).lower()
    # Claude models always require streaming
    if "claude" in model_lower:
        return True
    # Gemini 3 Pro models require streaming
    if "gemini-3-pro" in model_lower or "gemini-3.1-pro" in model_lower:
        return True
    return False


# ============================================
# HEADERS - Actualizado v1.4.3 (2026-02-05)
# ============================================
# Headers basados en opencode-antigravity-auth v1.4.3
# Formato Mozilla/5.0 completo como usa el plugin

ANTIGRAVITY_VERSION = "1.18.3"

# User-Agent formato completo (como el plugin v1.4.3)
ANTIGRAVITY_USER_AGENT = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Antigravity/{ANTIGRAVITY_VERSION} Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36"

# Headers estándar del plugin
def get_antigravity_headers() -> dict:
    """Headers estándar de Antigravity (formato plugin v1.4.3)."""
    metadata_platform = "WINDOWS" if sys.platform.startswith("win") else "MACOS"
    return {
        "User-Agent": ANTIGRAVITY_USER_AGENT,
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": f'{{"ideType":"ANTIGRAVITY","platform":"{metadata_platform}","pluginType":"GEMINI"}}',
    }


def get_load_headers(token: str) -> dict:
    """Headers for loadCodeAssist to match opencode-antigravity-auth."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "google-api-nodejs-client/9.15.1",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": get_antigravity_headers()["Client-Metadata"],
    }


def get_content_headers() -> dict:
    """
    Headers for content requests in Antigravity mode.
    Plugin behavior: send only User-Agent (plus auth/content-type set at call site).
    """
    return {"User-Agent": ANTIGRAVITY_USER_AGENT}

# Headers aleatorios (para compatibilidad, aunque el plugin usa el formato completo)
ANTIGRAVITY_USER_AGENTS = [
    ANTIGRAVITY_USER_AGENT,
]

ANTIGRAVITY_API_CLIENTS = [
    "google-cloud-sdk vscode_cloudshelleditor/0.1",
]

def get_randomized_headers() -> dict:
    """Genera headers aleatorios para cada request (evita detección)."""
    return get_content_headers()

# Headers estáticos para compatibilidad (ahora usa el formato completo)
ANTIGRAVITY_HEADERS = get_antigravity_headers()

# System instruction for Antigravity requests (CLIProxyAPI v6.6.89 compatibility)
ANTIGRAVITY_SYSTEM_INSTRUCTION = """<identity>
You are Antigravity, a powerful agentic AI coding assistant designed by the Google Deepmind team working on Advanced Agentic Coding.
You are pair programming with a USER to solve their coding task. The task may require creating a new codebase, modifying or debugging an existing codebase, or simply answering a question.
The USER will send you requests, which you must always prioritize addressing. Along with each USER request, we will attach additional metadata about their current state, such as what files they have open and where their cursor is.
This information may or may not be relevant to the coding task, it is up for you to decide.
</identity>

<tool_calling>
Call tools as you normally would. The following list provides additional guidance to help you avoid errors:
  - **Absolute paths only**. When using tools that accept file path arguments, ALWAYS use the absolute file path.
</tool_calling>

<web_application_development>
## Technology Stack,
Your web applications should be built using the following technologies:,
1. **Core**: Use HTML for structure and Javascript for logic.
2. **Styling (CSS)**: Use Vanilla CSS for maximum flexibility and control. Avoid using TailwindCSS unless the USER explicitly requests it; in this case, first confirm which TailwindCSS version to use.
3. **Web App**: If the USER specifies that they want a more complex web app, use a framework like Next.js or Vite. Only do this if the USER explicitly requests a web app.
4. **New Project Creation**: If you need to use a framework for a new app, use `npx` with the appropriate script, but there are some rules to follow:,
   - Use `npx -y` to automatically install the script and its dependencies
   - You MUST run the command with `--help` flag to see all available options first, 
   - Initialize the app in the current directory with `./` (example: `npx -y create-vite-app@latest ./`),
   - You should run in non-interactive mode so that the user doesn't need to input anything,
5. **Running Locally**: When running locally, use `npm run dev` or equivalent dev server. Only build the production bundle if the USER explicitly requests it or you are validating the code for correctness.

# Design Aesthetics,
1. **Use Rich Aesthetics**: The USER should be wowed at first glance by the design. Use best practices in modern web design (e.g. vibrant colors, dark modes, glassmorphism, and dynamic animations) to create a stunning first impression. Failure to do this is UNACCEPTABLE.
2. **Prioritize Visual Excellence**: Implement designs that will WOW the user and feel extremely premium:
		- Avoid generic colors (plain red, blue, green). Use curated, harmonious color palettes (e.g., HSL tailored colors, sleek dark modes).
   - Using modern typography (e.g., from Google Fonts like Inter, Roboto, or Outfit) instead of browser defaults.
		- Use smooth gradients,
		- Add subtle micro-animations for enhanced user experience,
3. **Use a Dynamic Design**: An interface that feels responsive and alive encourages interaction. Achieve this with hover effects and interactive elements. Micro-animations, in particular, are highly effective for improving user engagement.
4. **Premium Designs**. Make a design that feels premium and state of the art. Avoid creating simple minimum viable products.
4. **Don't use placeholders**. If you need an image, use your generate_image tool to create a working demonstration.,

## Implementation Workflow,
Follow this systematic approach when building web applications:,
1. **Plan and Understand**:,
		- Fully understand the user's requirements,
		- Draw inspiration from modern, beautiful, and dynamic web designs,
		- Outline the features needed for the initial version,
2. **Build the Foundation**:,
		- Start by creating/modifying `index.css`,
		- Implement the core design system with all tokens and utilities,
3. **Create Components**:,
		- Build necessary components using your design system,
		- Ensure all components use predefined styles, not ad-hoc utilities,
		- Keep components focused and reusable,
4. **Assemble Pages**:,
		- Update the main application to incorporate your design and components,
		- Ensure proper routing and navigation,
		- Implement responsive layouts,
5. **Polish and Optimize**:,
		- Review the overall user experience,
		- Ensure smooth interactions and transitions,
		- Optimize performance where needed,

## SEO Best Practices,
Automatically implement SEO best practices on every page:,
- **Title Tags**: Include proper, descriptive title tags for each page,
- **Meta Descriptions**: Add compelling meta descriptions that accurately summarize page content,
- **Heading Structure**: Use a single `<h1>` per page with proper heading hierarchy,
- **Semantic HTML**: Use appropriate HTML5 semantic elements,
- **Unique IDs**: Ensure all interactive elements have unique, descriptive IDs for browser testing,
- **Performance**: Ensure fast page load times through optimization,
CRITICAL REMINDER: AESTHETICS ARE VERY IMPORTANT. If your web app looks simple and basic then you have FAILED!
</web_application_development>
<ephemeral_message>
There will be an <EPHEMERAL_MESSAGE> appearing in the conversation at times. This is not coming from the user, but instead injected by the system as important information to pay attention to. 
Do not respond to nor acknowledge those messages, but do follow them strictly.
</ephemeral_message>


<communication_style>
- **Formatting**. Format your responses in github-style markdown to make your responses easier for the USER to parse. For example, use headers to organize your responses and bolded or italicized text to highlight important keywords. Use backticks to format file, directory, function, and class names. If providing a URL to the user, format this in markdown as well, for example `[label](example.com)`.
- **Proactiveness**. As an agent, you are allowed to be proactive, but only in the course of completing the user's task. For example, if the user asks you to add a new component, you can edit the code, verify build and test statuses, and take any other obvious follow-up actions, such as performing additional research. However, avoid surprising the user. For example, if the user asks HOW to approach something, you should answer their question and instead of jumping into editing a file.
- **Helpfulness**. Respond like a helpful software engineer who is explaining your work to a friendly collaborator on the project. Acknowledge mistakes or any backtracking you do as a result of new information.
- **Ask for clarification**. If you are unsure about the USER's intent, always ask for clarification rather than making assumptions.
</communication_style>"""


@dataclass
class GenerateResponse:
    """Response from generateContent API."""
    text: str
    model_version: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str
    trace_id: str


class AntigravityClient:
    """Client for Gemini API using Antigravity/OpenCode credentials."""
    
    def __init__(self, model: str = DEFAULT_MODEL, account_index: Optional[int] = None):
        self.model = model
        # Use mapped account for specific models if no explicit account_index provided
        if account_index is None:
            resolved_model = _strip_variant(_resolve_alias(model))
            self.account_index = MODEL_ACCOUNT_MAPPING.get(resolved_model)
        else:
            self.account_index = account_index
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0
        self._project_id: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._load_cache()
    
    def _find_accounts_file(self) -> Path:
        """Find the antigravity-accounts.json file."""
        for path in OPENCODE_ACCOUNTS_PATHS:
            if path.exists():
                return path
        raise FileNotFoundError(
            "Antigravity accounts not found. Searched:\n" +
            "\n".join(f"  - {p}" for p in OPENCODE_ACCOUNTS_PATHS) +
            "\n\nPlease configure Antigravity in OpenCode first."
        )
    
    def _load_cache(self):
        """Load cached project_id and tokens."""
        if CACHE_PATH.exists():
            try:
                with open(CACHE_PATH) as f:
                    cache = json.load(f)
                    self._project_id = cache.get("project_id")
                    # Don't cache access tokens - they expire quickly
            except (json.JSONDecodeError, KeyError):
                pass
    
    def _save_cache(self):
        """Save project_id to cache."""
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump({"project_id": self._project_id}, f)
    
    def _get_refresh_token(self) -> str:
        """Get refresh token from Antigravity accounts."""
        if self._refresh_token:
            return self._refresh_token
            
        accounts_file = self._find_accounts_file()
        
        with open(accounts_file) as f:
            data = json.load(f)
        
        accounts = data.get("accounts", [])
        if not accounts:
            raise ValueError("No accounts configured in Antigravity")
        
        # Use specified account_index, or active account from file, or first one
        if self.account_index is not None:
            active_index = self.account_index
        else:
            active_index = data.get("activeIndex", 0)
        if active_index < 0 or active_index >= len(accounts):
            active_index = 0
        
        account = accounts[active_index]
        self._refresh_token = account.get("refreshToken")
        
        if not self._refresh_token:
            raise ValueError(f"No refreshToken for account: {account.get('email', 'unknown')}")
        
        # NO confiar en managedProjectId del archivo - puede ser un placeholder inválido
        # Siempre obtener el project ID real desde la API en _get_project_id()
        
        return self._refresh_token
    
    def _refresh_access_token(self) -> str:
        """Refresh the access token using OAuth2."""
        refresh_token = self._get_refresh_token()
        
        response = requests.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        )
        response.raise_for_status()
        
        data = response.json()
        
        if "error" in data:
            raise RuntimeError(f"OAuth error: {data.get('error_description', data['error'])}")
        
        self._access_token = data["access_token"]
        # Token expires in ~1 hour, refresh 5 minutes early
        self._token_expiry = time.time() + data.get("expires_in", 3600) - 300
        
        assert self._access_token is not None
        return self._access_token
    
    def _get_access_token(self) -> str:
        """Get a valid access token, refreshing if needed."""
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token
        return self._refresh_access_token()
    
    def _get_project_id(self, force_refresh: bool = False) -> str:
        """Get the user's project ID from Code Assist.
        
        Args:
            force_refresh: Si es True, ignora el cache y obtiene el project_id de la API
        """
        if self._project_id and not force_refresh:
            return self._project_id
        
        token = self._get_access_token()
        url = f"{CODE_ASSIST_ENDPOINT}/{CODE_ASSIST_API_VERSION}:loadCodeAssist"
        
        response = requests.post(
            url,
            headers=get_load_headers(token),
            json={
                "metadata": {
                    "ideType": "ANTIGRAVITY",
                    "platform": "WINDOWS" if sys.platform.startswith("win") else "MACOS",
                    "pluginType": "GEMINI",
                }
            }
        )
        response.raise_for_status()
        
        data = response.json()
        self._project_id = data.get("cloudaicompanionProject")
        
        if not self._project_id:
            raise ValueError("Could not get project ID from Code Assist")
        
        self._save_cache()
        return self._project_id
    
    def _invalidate_project_id_cache(self):
        """Invalida el cache de project_id para forzar una recarga desde la API."""
        self._project_id = None
        if CACHE_PATH.exists():
            try:
                CACHE_PATH.unlink()
            except OSError:
                pass
    
    def get_account_info(self) -> Dict[str, Any]:
        """Get info about the active Antigravity account."""
        accounts_file = self._find_accounts_file()
        
        with open(accounts_file) as f:
            data = json.load(f)
        
        accounts = data.get("accounts", [])
        # Usar account_index si está configurado, sino active_index del archivo
        if self.account_index is not None:
            active_index = self.account_index
        else:
            active_index = data.get("activeIndex", 0)
        
        if not accounts:
            return {"error": "No accounts configured"}
        
        account = accounts[active_index]
        return {
            "email": account.get("email"),
            "project_id": account.get("managedProjectId") or self._get_project_id(),
            "is_rate_limited": account.get("isRateLimited", False),
            "accounts_file": str(accounts_file),
        }
    
    def generate_content(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        response_mime_type: Optional[str] = None,
    ) -> GenerateResponse:
        """
        Generate content using the Gemini API.
        
        Args:
            prompt: The user prompt
            system_instruction: Optional system instruction
            history: Optional conversation history (list of {"role": "user"|"model", "content": str})
            temperature: Optional temperature (0.0-2.0)
            max_output_tokens: Optional max tokens in response
            response_mime_type: Optional MIME type for response (e.g., "application/json")
        
        Returns:
            GenerateResponse with the generated text and metadata
        """
        # Claude and Gemini Pro models require the streaming endpoint
        # Use streaming internally and accumulate the response
        if requires_streaming(self.model):
            return self._generate_content_via_streaming(
                prompt, system_instruction, history, temperature, max_output_tokens, response_mime_type
            )
        
        return self._generate_content_non_streaming(
            prompt, system_instruction, history, temperature, max_output_tokens, response_mime_type
        )
    
    def _generate_content_via_streaming(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        response_mime_type: Optional[str] = None,
        _retry_on_429: bool = True,
    ) -> GenerateResponse:
        """Internal: Generate content using streaming endpoint and accumulate response."""
        text_parts = []
        model_version = self.model
        usage = {}
        finish_reason = "UNKNOWN"
        trace_id = ""
        
        token = self._get_access_token()
        project_id = self._get_project_id()
        
        url = f"{CODE_ASSIST_ENDPOINT}/{CODE_ASSIST_API_VERSION}:streamGenerateContent"
        
        # Build contents
        contents = []
        if history:
            for msg in history:
                contents.append({
                    "role": msg["role"],
                    "parts": [{"text": msg["content"]}]
                })
        contents.append({
            "role": "user",
            "parts": [{"text": prompt}]
        })
        
        # Build request
        request_body: Dict[str, Any] = {"contents": contents}
        
        # Add system instruction
        if system_instruction:
            combined_instruction = ANTIGRAVITY_SYSTEM_INSTRUCTION + "\n\n" + system_instruction
        else:
            combined_instruction = ANTIGRAVITY_SYSTEM_INSTRUCTION
        
        request_body["systemInstruction"] = {
            "parts": [{"text": combined_instruction}]
        }

        # Add generation config
        generation_config = get_model_config(self.model)
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens
        if generation_config:
            request_body["generationConfig"] = generation_config
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                **get_randomized_headers(),
                **get_extra_headers(self.model),
            },
            params={"alt": "sse"},
            json={
                "model": _resolve_api_model_name(self.model),
                "project": project_id,
                "request": request_body,
                "requestType": "agent",
            },
            stream=True,
        )

        
        if response.status_code != 200:
            error_data = response.json().get("error", {})
            error_code = error_data.get('code')
            error_msg = error_data.get('message', '')
            
            # Si es error 429 y es el primer intento, puede ser project_id cacheado incorrecto
            # Invalidar cache, obtener project_id fresco y reintentar una vez
            if error_code == 429 and _retry_on_429:
                self._invalidate_project_id_cache()
                self._get_project_id(force_refresh=True)
                return self._generate_content_via_streaming(
                    prompt, system_instruction, history, temperature,
                    max_output_tokens, response_mime_type, _retry_on_429=False
                )
            
            raise RuntimeError(
                f"API Error {error_code}: {error_msg}"
            )
        
        # Parse SSE stream and accumulate text
        buffer = []
        resolved_model = _resolve_alias(self.model)
        base_model = _strip_variant(resolved_model)
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith("data: "):
                buffer.append(line[6:].strip())
            elif line == "" and buffer:
                try:
                    data = json.loads("\n".join(buffer))
                    resp = data.get("response", {})
                    
                    # Update trace_id
                    if data.get("traceId"):
                        trace_id = data.get("traceId", "")
                    
                    for candidate in resp.get("candidates", []):
                        # Get finish reason from last chunk
                        if candidate.get("finishReason"):
                            finish_reason = candidate.get("finishReason")
                        for part in candidate.get("content", {}).get("parts", []):
                            if "text" in part:
                                text_parts.append(part["text"])
                    
                    # Get usage from last chunk
                    if resp.get("usageMetadata"):
                        usage = resp.get("usageMetadata", {})
                    if resp.get("modelVersion"):
                        model_version = base_model
                        
                except json.JSONDecodeError:
                    pass
                buffer = []
        
        return GenerateResponse(
            text="".join(text_parts),
            model_version=model_version,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            total_tokens=usage.get("totalTokenCount", 0),
            finish_reason=finish_reason,
            trace_id=trace_id,
        )
    
    def _generate_content_non_streaming(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        response_mime_type: Optional[str] = None,
        _retry_on_429: bool = True,
    ) -> GenerateResponse:
        """Internal: Generate content using non-streaming endpoint."""
        token = self._get_access_token()
        project_id = self._get_project_id()
        
        url = f"{CODE_ASSIST_ENDPOINT}/{CODE_ASSIST_API_VERSION}:generateContent"
        
        # Build contents
        contents = []
        
        # Add history if provided
        if history:
            for msg in history:
                contents.append({
                    "role": msg["role"],
                    "parts": [{"text": msg["content"]}]
                })
        
        # Add current prompt
        contents.append({
            "role": "user",
            "parts": [{"text": prompt}]
        })
        
        # Build request
        request_body: Dict[str, Any] = {
            "contents": contents,
        }
        
        # Add system instruction with Antigravity identity (CLIProxyAPI v6.6.89 compatibility)
        # Always inject ANTIGRAVITY_SYSTEM_INSTRUCTION, prepend user's system instruction if provided
        if system_instruction:
            combined_instruction = ANTIGRAVITY_SYSTEM_INSTRUCTION + "\n\n" + system_instruction
        else:
            combined_instruction = ANTIGRAVITY_SYSTEM_INSTRUCTION

        request_body["systemInstruction"] = {
            "parts": [{"text": combined_instruction}]
        }

        # Add generation config - start with model-specific config (thinkingConfig, etc.)
        generation_config = get_model_config(self.model)
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens

        if generation_config:
            request_body["generationConfig"] = generation_config

        api_model = _resolve_api_model_name(self.model)

        # Make request with requestType: "agent" (CLIProxyAPI v6.6.89 compatibility)
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                **get_randomized_headers(),
                **get_extra_headers(self.model),
            },
            json={
                "model": api_model,
                "project": project_id,
                "request": request_body,
                "requestType": "agent",
            }
        )
        
        if response.status_code != 200:
            error_data = response.json().get("error", {})
            error_code = error_data.get('code')
            error_msg = error_data.get('message', '')
            
            # Si es error 429 y es el primer intento, puede ser project_id cacheado incorrecto
            # Invalidar cache, obtener project_id fresco y reintentar una vez
            if error_code == 429 and _retry_on_429:
                self._invalidate_project_id_cache()
                self._get_project_id(force_refresh=True)
                return self._generate_content_non_streaming(
                    prompt, system_instruction, history, temperature,
                    max_output_tokens, response_mime_type, _retry_on_429=False
                )
            
            raise RuntimeError(
                f"API Error {error_code}: {error_msg}"
            )
        
        data = response.json()
        resp = data.get("response", {})
        
        # Extract text from response
        candidates = resp.get("candidates", [])
        if not candidates:
            raise ValueError("No candidates in response")
        
        text_parts = []
        for part in candidates[0].get("content", {}).get("parts", []):
            if "text" in part:
                text_parts.append(part["text"])
        
        text = "".join(text_parts)
        
        # Extract usage metadata
        usage = resp.get("usageMetadata", {})
        
        base_model = _strip_variant(_resolve_alias(self.model))
        return GenerateResponse(
            text=text,
            model_version=resp.get("modelVersion", base_model),
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            total_tokens=usage.get("totalTokenCount", 0),
            finish_reason=candidates[0].get("finishReason", "UNKNOWN"),
            trace_id=data.get("traceId", ""),
        )
    
    def generate_content_with_image(
        self,
        prompt: str,
        image_data: bytes,
        image_type: str = "image/jpeg",
        system_instruction: Optional[str] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        _retry_on_429: bool = True,
    ) -> GenerateResponse:
        """
        Generate content with an image using the Gemini API (vision).
        
        Args:
            prompt: The user prompt describing what to do with the image
            image_data: Raw bytes of the image
            image_type: MIME type of the image (image/jpeg, image/png, etc.)
            system_instruction: Optional system instruction
            temperature: Optional temperature (0.0-2.0)
            max_output_tokens: Optional max tokens in response
        
        Returns:
            GenerateResponse with the generated text and metadata
        """
        import base64
        
        token = self._get_access_token()
        project_id = self._get_project_id()
        
        url = f"{CODE_ASSIST_ENDPOINT}/{CODE_ASSIST_API_VERSION}:generateContent"
        
        # Encode image to base64
        image_b64 = base64.b64encode(image_data).decode('utf-8')
        
        # Build contents with image
        contents = [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {
                    "inlineData": {
                        "mimeType": image_type,
                        "data": image_b64
                    }
                }
            ]
        }]
        
        # Build request
        request_body: Dict[str, Any] = {
            "contents": contents,
        }
        
        # Add system instruction with Antigravity identity (CLIProxyAPI v6.6.89 compatibility)
        if system_instruction:
            combined_instruction = ANTIGRAVITY_SYSTEM_INSTRUCTION + "\n\n" + system_instruction
        else:
            combined_instruction = ANTIGRAVITY_SYSTEM_INSTRUCTION

        request_body["systemInstruction"] = {
            "parts": [{"text": combined_instruction}]
        }

        # Add generation config - start with model-specific config (thinkingConfig, etc.)
        generation_config = get_model_config(self.model)
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens

        if generation_config:
            request_body["generationConfig"] = generation_config

        api_model = _resolve_api_model_name(self.model)

        # Make request with requestType: "agent" (CLIProxyAPI v6.6.89 compatibility)
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                **get_randomized_headers(),
                **get_extra_headers(self.model),
            },
            json={
                "model": api_model,
                "project": project_id,
                "request": request_body,
                "requestType": "agent",
            }
        )
        
        if response.status_code != 200:
            error_data = response.json().get("error", {})
            error_code = error_data.get('code')
            error_msg = error_data.get('message', '')
            
            # Si es error 429 y es el primer intento, puede ser project_id cacheado incorrecto
            # Invalidar cache, obtener project_id fresco y reintentar una vez
            if error_code == 429 and _retry_on_429:
                self._invalidate_project_id_cache()
                self._get_project_id(force_refresh=True)
                return self.generate_content_with_image(
                    prompt, image_data, image_type, system_instruction,
                    temperature, max_output_tokens, _retry_on_429=False
                )
            
            raise RuntimeError(
                f"API Error {error_code}: {error_msg}"
            )
        
        data = response.json()
        resp = data.get("response", {})
        
        # Extract text from response
        candidates = resp.get("candidates", [])
        if not candidates:
            raise ValueError("No candidates in response")
        
        text_parts = []
        for part in candidates[0].get("content", {}).get("parts", []):
            if "text" in part:
                text_parts.append(part["text"])
        
        text = "".join(text_parts)
        
        # Extract usage metadata
        usage = resp.get("usageMetadata", {})
        
        base_model = _strip_variant(_resolve_alias(self.model))
        return GenerateResponse(
            text=text,
            model_version=resp.get("modelVersion", base_model),
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            total_tokens=usage.get("totalTokenCount", 0),
            finish_reason=candidates[0].get("finishReason", "UNKNOWN"),
            trace_id=data.get("traceId", ""),
        )
    
    def generate_content_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        response_mime_type: Optional[str] = None,
    ):
        """
        Generate content with streaming using the Gemini API.
        
        Yields text chunks as they arrive.
        """
        token = self._get_access_token()
        project_id = self._get_project_id()
        
        url = f"{CODE_ASSIST_ENDPOINT}/{CODE_ASSIST_API_VERSION}:streamGenerateContent"
        
        # Build contents
        contents = []
        if history:
            for msg in history:
                contents.append({
                    "role": msg["role"],
                    "parts": [{"text": msg["content"]}]
                })
        contents.append({
            "role": "user",
            "parts": [{"text": prompt}]
        })
        
        request_body: Dict[str, Any] = {"contents": contents}
        
        # Add system instruction with Antigravity identity (CLIProxyAPI v6.6.89 compatibility)
        if system_instruction:
            combined_instruction = ANTIGRAVITY_SYSTEM_INSTRUCTION + "\n\n" + system_instruction
        else:
            combined_instruction = ANTIGRAVITY_SYSTEM_INSTRUCTION
        
        request_body["systemInstruction"] = {
            "parts": [{"text": combined_instruction}]
        }

        # Add generation config - start with model-specific config (thinkingConfig, etc.)
        generation_config = get_model_config(self.model)
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens
        if response_mime_type is not None:
            generation_config["responseMimeType"] = response_mime_type

        if generation_config:
            request_body["generationConfig"] = generation_config
        # Make request with requestType: "agent" (CLIProxyAPI v6.6.89 compatibility)
        api_model = _resolve_api_model_name(self.model)
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                **get_randomized_headers(),
                **get_extra_headers(self.model),
            },
            params={"alt": "sse"},
            json={
                "model": api_model,
                "project": project_id,
                "request": request_body,
                "requestType": "agent",
            },
            stream=True,
        )
        
        if response.status_code != 200:
            error_data = response.json().get("error", {})
            raise RuntimeError(
                f"API Error {error_data.get('code')}: {error_data.get('message')}"
            )
        
        buffer = []
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith("data: "):
                buffer.append(line[6:].strip())
            elif line == "" and buffer:
                try:
                    data = json.loads("\n".join(buffer))
                    resp = data.get("response", {})
                    for candidate in resp.get("candidates", []):
                        for part in candidate.get("content", {}).get("parts", []):
                            if "text" in part:
                                yield part["text"]
                except json.JSONDecodeError:
                    pass
                buffer = []
    
    def list_models(self) -> Dict[str, str]:
        """Return available models and descriptions."""
        return MODELS.copy()


def main():
    """Command line interface."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Antigravity API Client - Use OpenCode/Antigravity quota for Gemini API calls"
    )
    parser.add_argument("prompt", nargs="?", help="The prompt to send")
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL}). Use --list-models to see options."
    )
    parser.add_argument(
        "--system", "-s",
        help="System instruction"
    )
    parser.add_argument(
        "--temperature", "-t",
        type=float,
        help="Temperature (0.0-2.0)"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        help="Max output tokens"
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream the response"
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List available models"
    )
    parser.add_argument(
        "--account",
        action="store_true",
        help="Show active account info"
    )
    parser.add_argument(
        "--use-account", "-a",
        type=int,
        help="Use account by index (0, 1, 2, ...)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    
    args = parser.parse_args()
    
    try:
        client = AntigravityClient(model=args.model, account_index=args.use_account)
        
        if args.list_models:
            print("\nAvailable models:")
            for model, desc in MODELS.items():
                marker = " (default)" if model == DEFAULT_MODEL else ""
                print(f"  {model}: {desc}{marker}")
            return
        
        if args.account:
            info = client.get_account_info()
            print("\nAntigravity Account Info:")
            print(f"  Email: {info.get('email', 'unknown')}")
            print(f"  Project ID: {info.get('project_id', 'unknown')}")
            print(f"  Rate Limited: {info.get('is_rate_limited', False)}")
            print(f"  Config File: {info.get('accounts_file', 'unknown')}")
            return
        
        if not args.prompt:
            parser.print_help()
            return
        
        if args.stream:
            for chunk in client.generate_content_stream(
                args.prompt,
                system_instruction=args.system,
                temperature=args.temperature,
                max_output_tokens=args.max_tokens,
            ):
                print(chunk, end="", flush=True)
            print()
        else:
            response = client.generate_content(
                args.prompt,
                system_instruction=args.system,
                temperature=args.temperature,
                max_output_tokens=args.max_tokens,
            )
            
            if args.json:
                output = {
                    "text": response.text,
                    "model": response.model_version,
                    "usage": {
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                        "total_tokens": response.total_tokens,
                    },
                    "finish_reason": response.finish_reason,
                    "trace_id": response.trace_id,
                }
                print(json.dumps(output, indent=2, ensure_ascii=False))
            else:
                print(response.text)
                print(f"\n---")
                print(f"Model: {response.model_version}")
                print(f"Tokens: {response.prompt_tokens} in / {response.completion_tokens} out / {response.total_tokens} total")
    
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
