from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from intent_handler import IntentRuntime, StreamHandler, _load_runtime_config, build_default_dispatch_adapter
from intent_handler.run_intent_runtime import _default_intent_dir, _load_backend_api, _load_secrets_file
from utils import configure_logging


@dataclass
class HarnessConnectionConfig:
    """Connection parameters for the real backend + LLM, mirroring run_intent_runtime.py's CLI args.

    Deliberately field-for-field compatible with the args in the
    "Claude-IntentHandler-LakeEncino" launch.json config so that config can be
    pasted straight into a CLI invocation of run_real_device_tests.py.
    """

    backend_api_classpath: str
    backend_api_base_url: str
    backend_api_username: str
    backend_api_password: str
    runtime_config_path: Path
    secrets_file: Path | None = None
    intent_dir: Path | None = None
    path_to_data_directory: Path | None = None
    json_output: bool = True
    log_level: str | None = "ERROR"


def build_runtime(config: HarnessConnectionConfig) -> IntentRuntime:
    """Construct a real, network-connected IntentRuntime.

    Mirrors the startup sequence in run_intent_runtime.main() step for step
    (load secrets -> load runtime profile -> build LLM adapter -> load backend
    API -> construct IntentRuntime) by importing and reusing those exact
    functions, rather than re-implementing backend/LLM bootstrapping here.
    """
    configure_logging(level=config.log_level, force=True)

    intent_dir = config.intent_dir or _default_intent_dir()
    runtime_config_path = Path(config.runtime_config_path).expanduser().resolve()
    secrets_env = _load_secrets_file(config.secrets_file) if config.secrets_file else None

    runtime_config = _load_runtime_config(path=str(runtime_config_path), stream_handler=None)
    llm_adapter = build_default_dispatch_adapter(runtime_config, env=secrets_env)

    nucore_interface = _load_backend_api(
        classpath=config.backend_api_classpath,
        base_url=config.backend_api_base_url,
        username=config.backend_api_username,
        password=config.backend_api_password,
        json_output=config.json_output,
    )
    if nucore_interface is None:
        raise ValueError(
            "Backend API failed to load; check --backend-api-* arguments (base-url, classpath, username, password)"
        )

    path_to_data_directory = config.path_to_data_directory or (Path(__file__).resolve().parents[1] / ".data")

    return IntentRuntime(
        intent_handler_directory=intent_dir,
        llm_client=llm_adapter,
        nucore_interface=nucore_interface,
        runtime_config_path=runtime_config_path,
        path_to_data_directory=path_to_data_directory,
        stream_handler=StreamHandler(),
    )
