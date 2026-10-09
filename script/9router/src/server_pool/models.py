"""Data classes and HTTP client for 9Router API."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from .console import error, info, success, warning

DEFAULT_TIMEOUT = 15


@dataclass
class Instance:
    name: str
    host: str
    password: str
    docker_host: str = ""

    @property
    def base_url(self) -> str:
        return f"http://{self.host}"

    @property
    def docker_url(self) -> str:
        return f"http://{self.docker_host}" if self.docker_host else self.base_url


def master_instance(config: dict[str, Any]) -> Instance:
    data = config["master"]

    return Instance(
        name=data["name"],
        host=data["host"],
        password=data["password"],
    )


def slave_instances(config: dict[str, Any]) -> list[Instance]:
    result = []

    defaults = config["defaults"]

    for item in config["slaves"]:
        result.append(
            Instance(
                name=item["name"],
                host=item["host"],
                docker_host=item.get("docker_host", ""),
                password=item.get(
                    "password",
                    defaults.get("password", "123456")
                ),
            )
        )

    return result


class RouterClient:
    def __init__(
        self,
        instance: Instance,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.instance = instance
        self.timeout = timeout
        self.session = requests.Session()
        self.logged_in = False

    # ------------------------------------------------------------------------
    # LOGIN
    # ------------------------------------------------------------------------

    def login(self) -> bool:
        try:
            response = self.session.post(
                f"{self.instance.base_url}/api/auth/login",
                json={
                    "password": self.instance.password
                },
                timeout=self.timeout,
            )

            response.raise_for_status()

            data = response.json()

        except requests.RequestException as exc:
            warning(
                f"{self.instance.name}: erro de conexão/login: {exc}"
            )
            return False

        except ValueError:
            warning(
                f"{self.instance.name}: resposta inválida no login"
            )
            return False

        self.logged_in = bool(data.get("success"))

        return self.logged_in

    # ------------------------------------------------------------------------
    # REQUIRE LOGIN
    # ------------------------------------------------------------------------

    def require_login(self) -> None:
        if not self.logged_in:
            if not self.login():
                raise RuntimeError(
                    f"login falhou: {self.instance.name}"
                )

    # ------------------------------------------------------------------------
    # PROVIDER NODES
    # ------------------------------------------------------------------------

    def get_provider_nodes(self) -> list[dict[str, Any]]:
        self.require_login()

        response = self.session.get(
            f"{self.instance.base_url}/api/provider-nodes",
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("nodes", [])

    def create_provider_node(
        self,
        name: str,
        prefix: str,
        base_url: str,
        node_type: str = "anthropic-compatible",
    ) -> dict[str, Any]:
        self.require_login()

        response = self.session.post(
            f"{self.instance.base_url}/api/provider-nodes",
            json={
                "name": name,
                "type": node_type,
                "prefix": prefix,
                "baseUrl": base_url,
            },
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("node", {})

    def delete_provider_node(self, node_id: str) -> bool:
        self.require_login()

        response = self.session.delete(
            f"{self.instance.base_url}/api/provider-nodes/{node_id}",
            timeout=self.timeout,
        )

        response.raise_for_status()

        return True

    # ------------------------------------------------------------------------
    # PROVIDERS (CONNECTIONS)
    # ------------------------------------------------------------------------

    def get_providers(self) -> list[dict[str, Any]]:
        self.require_login()

        response = self.session.get(
            f"{self.instance.base_url}/api/providers",
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("connections", [])

    def create_provider(
        self,
        provider_type: str,
        name: str,
        api_key: str,
        base_url: str | None = None,
        priority: int = 1,
        default_model: str | None = None,
        provider_specific_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.require_login()

        payload: dict[str, Any] = {
            "provider": provider_type,
            "name": name,
            "apiKey": api_key,
            "priority": priority,
        }

        if default_model:
            payload["defaultModel"] = default_model

        if provider_specific_data:
            payload["providerSpecificData"] = provider_specific_data

        response = self.session.post(
            f"{self.instance.base_url}/api/providers",
            json=payload,
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        connection = data.get("connection", {})
        conn_id = connection.get("id")

        if base_url and conn_id:
            self.update_provider(
                conn_id,
                provider_specific_data={"baseUrl": base_url},
            )

        return connection

    def update_provider(
        self,
        provider_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.require_login()

        response = self.session.put(
            f"{self.instance.base_url}/api/providers/{provider_id}",
            json=kwargs,
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("connection", {})

    def delete_provider(self, provider_id: str) -> bool:
        self.require_login()

        response = self.session.delete(
            f"{self.instance.base_url}/api/providers/{provider_id}",
            timeout=self.timeout,
        )

        response.raise_for_status()

        return True

    def test_provider(self, provider_id: str, timeout: int | None = None) -> dict[str, Any]:
        self.require_login()

        response = self.session.post(
            f"{self.instance.base_url}/api/providers/{provider_id}/test",
            json={},
            timeout=timeout or self.timeout,
        )

        response.raise_for_status()

        return response.json()

    # ------------------------------------------------------------------------
    # CUSTOM MODELS
    # ------------------------------------------------------------------------

    def get_custom_models(self) -> list[dict[str, Any]]:
        self.require_login()

        response = self.session.get(
            f"{self.instance.base_url}/api/models/custom",
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("models", [])

    def add_custom_model(
        self,
        provider_alias: str,
        model_id: str,
        model_name: str,
        model_type: str = "llm",
    ) -> bool:
        self.require_login()

        response = self.session.post(
            f"{self.instance.base_url}/api/models/custom",
            json={
                "providerAlias": provider_alias,
                "id": model_id,
                "type": model_type,
                "name": model_name,
            },
            timeout=self.timeout,
        )

        response.raise_for_status()

        return True

    def delete_custom_model(
        self,
        provider_alias: str,
        model_id: str,
    ) -> bool:
        self.require_login()

        response = self.session.delete(
            f"{self.instance.base_url}/api/models/custom",
            params={
                "providerAlias": provider_alias,
                "id": model_id,
            },
            timeout=self.timeout,
        )

        response.raise_for_status()

        return True

    # ------------------------------------------------------------------------
    # API KEYS
    # ------------------------------------------------------------------------

    def get_api_keys(self) -> list[dict[str, Any]]:
        self.require_login()

        response = self.session.get(
            f"{self.instance.base_url}/api/keys",
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("keys", [])

    def create_api_key(
        self,
        name: str,
    ) -> dict[str, Any]:
        self.require_login()

        response = self.session.post(
            f"{self.instance.base_url}/api/keys",
            json={
                "name": name,
            },
            timeout=self.timeout,
        )

        response.raise_for_status()

        return response.json()

    def delete_api_key(self, key_id: str) -> bool:
        self.require_login()

        response = self.session.delete(
            f"{self.instance.base_url}/api/keys/{key_id}",
            timeout=self.timeout,
        )

        response.raise_for_status()

        return True

    # ------------------------------------------------------------------------
    # COMBOS
    # ------------------------------------------------------------------------

    def get_combos(self) -> list[dict[str, Any]]:
        self.require_login()

        response = self.session.get(
            f"{self.instance.base_url}/api/combos",
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("combos", [])

    def create_combo(
        self,
        name: str,
        models: list[str],
    ) -> dict[str, Any]:
        self.require_login()

        response = self.session.post(
            f"{self.instance.base_url}/api/combos",
            json={
                "name": name,
                "models": models,
            },
            timeout=self.timeout,
        )

        response.raise_for_status()

        return response.json()

    def delete_combo(self, combo_id: str) -> bool:
        self.require_login()

        response = self.session.delete(
            f"{self.instance.base_url}/api/combos/{combo_id}",
            timeout=self.timeout,
        )

        response.raise_for_status()

        return True

    # ------------------------------------------------------------------------
    # SETTINGS
    # ------------------------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        self.require_login()

        response = self.session.get(
            f"{self.instance.base_url}/api/settings",
            timeout=self.timeout,
        )

        response.raise_for_status()

        return response.json()

    def update_settings(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.require_login()

        response = self.session.patch(
            f"{self.instance.base_url}/api/settings",
            json=kwargs,
            timeout=self.timeout,
        )

        response.raise_for_status()

        if not response.content:
            return {}

        return response.json()

    # ------------------------------------------------------------------------
    # MODELS
    # ------------------------------------------------------------------------

    def get_models(self) -> list[dict[str, Any]]:
        self.require_login()

        response = self.session.get(
            f"{self.instance.base_url}/api/models",
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("models", [])

    # ------------------------------------------------------------------------
    # BACKUP (export full state)
    # ------------------------------------------------------------------------

    def get_backup(self) -> dict[str, Any]:
        self.require_login()

        return {
            "settings": self.get_settings(),
            "providerConnections": self.get_providers(),
            "providerNodes": self.get_provider_nodes(),
            "apiKeys": [],
            "combos": self.get_combos(),
            "modelAliases": {},
            "customModels": self.get_custom_models(),
            "mitmAlias": {},
            "pricing": {},
        }


def wait_for_instance(
    instance: Instance,
    max_attempts: int = 20,
    delay: int = 3,
) -> bool:
    """Wait for an instance HTTP API to be ready."""
    info(f"Aguardando {instance.name} ({instance.base_url})...")
    client = RouterClient(instance)
    for attempt in range(max_attempts):
        try:
            response = client.session.post(
                f"{instance.base_url}/api/auth/login",
                json={"password": instance.password},
                timeout=5,
            )
            if response.status_code == 200:
                success(f"{instance.name}: API pronta")
                return True
        except requests.RequestException:
            pass
        info(f"{instance.name}: ainda inicializando... ({attempt + 1}/{max_attempts})")
        time.sleep(delay)
    warning(f"{instance.name}: pode não estar totalmente pronto")
    return False
