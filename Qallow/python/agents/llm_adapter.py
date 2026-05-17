"""
Pure Local Ollama LLM Adapter for Qallow
Zero cloud dependencies, zero paid APIs, zero external SDKs. Direct local HTTP to Ollama.
"""

import json
import logging
import os
import struct
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any, Generator
from dataclasses import dataclass
import lmdb

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s'
)
logger = logging.getLogger("LocalOllamaAdapter")


@dataclass
class LLMConfig:
    """Configuration for local Ollama adapter"""
    model_name: str = "gemma4:26b"
    base_url: str = "http://localhost:11434/api"
    temperature: float = 0.6
    max_tokens: int = 2048
    top_p: float = 0.9
    timeout: float = 300.0

    def __post_init__(self):
        if "QALLOW_OLLAMA_MODEL" in os.environ:
            self.model_name = os.environ["QALLOW_OLLAMA_MODEL"].strip()
        if "OLLAMA_BASE_URL" in os.environ:
            url = os.environ["OLLAMA_BASE_URL"].strip().rstrip("/")
            if not url.endswith("/api"):
                url += "/api"
            self.base_url = url
        if "QALLOW_LLM_TIMEOUT" in os.environ:
            try:
                self.timeout = float(os.environ["QALLOW_LLM_TIMEOUT"])
            except ValueError:
                pass



class LLMAdapter:
    """
    100% Sovereign Local LLM Adapter using built-in urllib
    Interacts directly with local Ollama daemon over HTTP
    """
    
    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()
        self._verify_connection()
    
    def _verify_connection(self):
        """Verify local Ollama daemon is running and has the required model."""
        url = f"{self.config.base_url}/tags"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                data = json.loads(resp.read().decode())
                models = [m["name"] for m in data.get("models", [])]
                logger.info(f"✓ Verified local Ollama daemon at {self.config.base_url}")
                logger.info(f"✓ Available sovereign models: {models}")
                if self.config.model_name not in models:
                    logger.warning(f"Model '{self.config.model_name}' not explicitly listed. Will attempt fallback.")
        except Exception as e:
            logger.error(f"✗ Could not connect to local Ollama at {url}: {e}")
            raise RuntimeError(f"Local Ollama daemon is not reachable at {url}") from e

    def _get_veyn_context(self) -> str:
        """Fetch live state variables from LMDB VEYN bridge, if available and enabled."""
        if not os.environ.get("QALLOW_CONTEXT_INJECT"):
            return ""
        try:
            db_path = os.path.join(os.path.dirname(__file__), '../../core/qallow-veyn-bridge/veyn_metrics')
            if not os.path.exists(db_path):
                return ""

            env = lmdb.open(db_path, readonly=True, max_dbs=1)
            db = env.open_db(b'veyn_metrics')
            context = []
            with env.begin(db=db) as txn:
                for key in [b'energy', b'risk', b'reward_mod', b'autonomy']:
                    val = txn.get(key)
                    if val:
                        float_val = struct.unpack('<d', val)[0]
                        context.append(f"{key.decode()}: {float_val:.2f}")

            if context:
                return f"[COGNITIVE STATE: {', '.join(context)}]\n"
        except Exception as e:
            logger.warning(f"Failed to read LMDB context: {e}")
        return ""

    def chat(self, message: str, system_prompt: Optional[str] = None) -> str:
        """Send a prompt to local Ollama and return the full completion."""
        url = f"{self.config.base_url}/chat"
        messages = []
        
        veyn_context = self._get_veyn_context()
        effective_system = veyn_context + (system_prompt or "")
        if effective_system.strip():
            messages.append({"role": "system", "content": effective_system.strip()})
        
        messages.append({"role": "user", "content": message})
        
        payload = {
            "model": self.config.model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
                "top_p": self.config.top_p
            }
        }
        
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                result = json.loads(resp.read().decode())
                return result.get("message", {}).get("content", "")
        except Exception as e:
            logger.error(f"Local Ollama chat request failed: {e}")
            raise

    def chat_stream(self, message: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Stream a response from local Ollama chunk by chunk."""
        url = f"{self.config.base_url}/chat"
        messages = []
        
        veyn_context = self._get_veyn_context()
        effective_system = veyn_context + (system_prompt or "")
        if effective_system.strip():
            messages.append({"role": "system", "content": effective_system.strip()})
        
        messages.append({"role": "user", "content": message})
        
        payload = {
            "model": self.config.model_name,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
                "top_p": self.config.top_p
            }
        }
        
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                for line in resp:
                    if line.strip():
                        chunk = json.loads(line.decode("utf-8"))
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
        except Exception as e:
            logger.error(f"Local Ollama stream failed: {e}")
            raise

    def set_temperature(self, temperature: float):
        self.config.temperature = temperature
        logger.info(f"Temperature set to {temperature}")
    
    def set_max_tokens(self, max_tokens: int):
        self.config.max_tokens = max_tokens
        logger.info(f"Max tokens set to {max_tokens}")
    
    def get_config(self) -> Dict[str, Any]:
        return {
            "model_name": self.config.model_name,
            "base_url": self.config.base_url,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "top_p": self.config.top_p,
            "timeout": self.config.timeout
        }


def create_llm_adapter(model_name: str = "gemma4:26b", 
                       base_url: str = "http://localhost:11434/api") -> LLMAdapter:
    config = LLMConfig(model_name=model_name, base_url=base_url)
    return LLMAdapter(config)


def chat(message: str, model_name: str = "gemma4:26b") -> str:
    adapter = create_llm_adapter(model_name=model_name)
    return adapter.chat(message)


def chat_stream(message: str, model_name: str = "gemma4:26b"):
    adapter = create_llm_adapter(model_name=model_name)
    yield from adapter.chat_stream(message)
