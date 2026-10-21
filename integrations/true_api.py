"""Sandbox-only True API transport. Demo codes are never sent to CRPT."""
import base64
import json
import threading
import time
from urllib.parse import quote
import httpx

SANDBOX = "https://markirovka.sandbox.crptech.ru"


class IntegrationError(ValueError):
    pass


class TrueApi:
    def __init__(self, config, transport=None):
        self.config = config
        self._token = config.true_api_token
        self._challenges = {}
        self._lock = threading.Lock()
        self.client = httpx.Client(base_url=SANDBOX, timeout=config.true_api_timeout,
                                   transport=transport, follow_redirects=False, trust_env=False)

    def close(self):
        self.client.close()

    def status(self):
        return {"enabled": self.config.true_api_enabled, "environment": "sandbox", "host": SANDBOX,
                "hasToken": bool(self._token), "documentProfileReady": bool(self.config.true_api_profile_reviewed
                    and self.config.true_api_document_type and self.config.true_api_product_group),
                "hardware": "simulator", "productionEnabled": False}

    def request(self, method, path, *, authenticated=True, **kwargs):
        if not path.startswith("/api/") or "//" in path or ".." in path:
            raise IntegrationError("Путь метода должен находиться внутри /api/ тестового контура.")
        if not self.config.true_api_enabled:
            raise IntegrationError("True API отключён. ИТ-специалист должен включить тестовый контур в .env.")
        headers = {"Accept": "application/json"}
        if authenticated:
            if not self._token:
                raise IntegrationError("Сначала выполните авторизацию тестовой электронной подписью.")
            headers["Authorization"] = "Bearer " + self._token
        try:
            response = self.client.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException:
            raise IntegrationError("Тайм-аут True API. Для отправленного документа результат неизвестен; не повторяйте отправку автоматически.") from None
        except httpx.HTTPError:
            raise IntegrationError("Не удалось подключиться к тестовому контуру. Проверьте сеть и сертификаты TLS.") from None
        if response.status_code == 401:
            self._token = ""
            raise IntegrationError("Сессия True API недействительна. Повторите авторизацию.")
        if response.status_code >= 300:
            raise IntegrationError(f"True API вернул HTTP {response.status_code}. Проверьте права и актуальную документацию.")
        try:
            return response.json()
        except ValueError:
            raise IntegrationError("True API вернул неожиданный формат ответа.") from None

    def challenge(self):
        data = self.request("GET", "/api/v3/true-api/auth/key", authenticated=False)
        if not isinstance(data, dict) or not isinstance(data.get("uuid"), str) or not isinstance(data.get("data"), str):
            raise IntegrationError("В ответе авторизации отсутствуют uuid/data.")
        with self._lock:
            self._challenges = {k: v for k,v in self._challenges.items() if v > time.monotonic()}
            self._challenges[data["uuid"]] = time.monotonic() + 300
        return {"uuid": data["uuid"], "data": data["data"], "signatureType": "attached", "expiresIn": 300}

    def authenticate(self, uuid, signature):
        validate_signature(signature)
        with self._lock:
            expiry = self._challenges.pop(uuid, 0)
        if expiry < time.monotonic():
            raise IntegrationError("Запрос авторизации истёк или уже использован. Получите новый.")
        payload = {"uuid": uuid, "data": signature}
        if self.config.true_api_inn:
            payload["inn"] = self.config.true_api_inn
        response = self.request("POST", "/api/v3/true-api/auth/simpleSignIn", authenticated=False, json=payload)
        if not isinstance(response, dict) or not isinstance(response.get("token"), str):
            raise IntegrationError("Токен не получен. Проверьте регистрацию сертификата, права и МЧД.")
        self._token = response["token"]
        return self.status()

    def code_info(self, codes):
        if not codes or len(codes) > 100 or any(not isinstance(c,str) or not c or len(c)>512 or "DEMO-" in c for c in codes):
            raise IntegrationError("Укажите от 1 до 100 настоящих тестовых КИ; идентификаторы DEMO отправлять нельзя.")
        return self.request("POST", "/api/v3/true-api/cises/info", json=codes)

    def submit_signed(self, raw, signature):
        # Sign externally and keep the exact signed bytes. Never reserialize signed JSON.
        if not self.status()["documentProfileReady"]:
            raise IntegrationError("Профиль отправки не согласован. Сверьте тип документа и товарную группу с актуальным True API.")
        validate_signature(signature)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise IntegrationError("Документ должен содержать JSON в UTF-8.") from None
        if not isinstance(payload, dict) or "DEMO-" in json.dumps(payload,ensure_ascii=False):
            raise IntegrationError("Учебные идентификаторы DEMO нельзя отправлять в True API.")
        envelope = {"document_format": "MANUAL", "product_document": base64.b64encode(raw).decode("ascii"),
                    "type": self.config.true_api_document_type, "signature": signature}
        return self.request("POST", self.config.true_api_document_path, params={"pg": self.config.true_api_product_group}, json=envelope)

    def document_info(self, external_id):
        if not external_id or len(external_id)>128 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in external_id):
            raise IntegrationError("Неверный идентификатор документа.")
        return self.request("GET", self.config.true_api_status_path.replace("{document_id}",quote(external_id,safe="")))


def validate_signature(value):
    try:
        if not isinstance(value,str) or not 4 <= len(value) <= 2_000_000:
            raise ValueError()
        decoded = base64.b64decode(value,validate=True)
        if not decoded or decoded[0] != 0x30:
            raise ValueError()
    except (ValueError,TypeError):
        raise IntegrationError("Ожидается CMS-подпись в base64 без переносов строк. Криптографическую проверку выполняет ГИС МТ.") from None
