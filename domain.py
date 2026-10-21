"""Demo aggregation rules, independent of HTTP, UI and device adapters."""
from copy import deepcopy
from datetime import datetime
import re
from uuid import uuid4


class RuleError(ValueError):
    pass


def initial_state():
    return {"version": 2, "sequence": 0, "revision": 0,
            "line": "LINE-1", "batch": "ДЕМО-ПАРТИЯ-01", "operator": "demo-operator",
            "settings": {"recipes": {"1L": 15, "5L": 3}, "palletSize": 50},
            "product": "1L", "draft": [], "palletDraft": [], "palletTarget": None,
            "boxes": [], "pallets": [], "documents": [], "events": [], "printJobs": [], "online": True}


def stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(s, message, level="info"):
    s["events"].insert(0, {"id": str(uuid4()), "at": stamp(), "message": message,
                            "level": level, "actor": s.get("operator", "demo-operator")})


def identifier(s, kind):
    s["sequence"] += 1
    return f"DEMO-{kind}-{s['sequence']:06d}"


def add_code(s, code):
    code = str(code).strip()
    match = re.fullmatch(r"DEMO-(1L|5L)-[A-Za-z0-9-]{1,60}", code)
    if not match:
        raise RuleError("В демо принимаются коды вида DEMO-1L-000001 или DEMO-5L-000001.")
    if match[1] != s["product"]:
        raise RuleError("Код относится к другой продукции. Проверьте выбранный рецепт.")
    if code in s["draft"] or any(code in box["codes"] for box in s["boxes"]):
        raise RuleError("Этот код уже считан или включён в закрытый короб.")
    if len(s["draft"]) >= s["settings"]["recipes"][s["product"]]:
        raise RuleError("Короб уже заполнен. Закройте его перед следующим считыванием.")
    s["draft"].append(code)


def fill(s):
    target = s["settings"]["recipes"][s["product"]]
    used = set(s["draft"]) | {c for b in s["boxes"] for c in b["codes"]}
    while len(s["draft"]) < target:
        code = identifier(s, s["product"])
        if code not in used:
            add_code(s, code)
            used.add(code)


def document(s, aggregate, kind):
    s["documents"].insert(0, {"id": identifier(s, "DOC"), "aggregate": aggregate,
                                  "kind": kind, "status": "queued", "attempts": 0,
                                  "created": stamp(), "message": "Ожидает отправки в имитатор"})


def close_box(s):
    target = s["settings"]["recipes"][s["product"]]
    if len(s["draft"]) != target:
        raise RuleError(f"Для закрытия короба необходимо {target} кодов.")
    box = {"id": identifier(s, "BOX"), "product": s["product"], "codes": list(s["draft"]),
           "created": stamp(), "pallet": None, "prints": 0, "line": s["line"], "batch": s["batch"]}
    s["boxes"].insert(0, box)
    s["draft"] = []
    document(s, box["id"], "box")
    s["printJobs"].insert(0, {"id": str(uuid4()), "aggregate": box["id"], "status": "ready", "created": stamp()})
    log(s, f"Закрыт короб {box['id']}: {target} бутылок")
    return box


def add_box(s, box_id):
    box = next((b for b in s["boxes"] if b["id"] == box_id), None)
    if not box:
        raise RuleError("Короб не найден в локальном реестре.")
    if box["pallet"] or box_id in s["palletDraft"]:
        raise RuleError("Короб уже включён в паллету. Повторное добавление запрещено.")
    if s["palletDraft"]:
        first = next(b for b in s["boxes"] if b["id"] == s["palletDraft"][0])
        if any(box[k] != first[k] for k in ("product", "line", "batch")):
            raise RuleError("В демо паллета собирается из коробов одной линии, продукции и партии.")
    if not s["palletDraft"]:
        s["palletTarget"] = s["settings"]["palletSize"]
    if len(s["palletDraft"]) >= s["palletTarget"]:
        raise RuleError("Паллета заполнена. Закройте её.")
    s["palletDraft"].append(box_id)


def apply(state, action, data):
    """Return a new state atomically; failed actions cannot partially modify it."""
    s = deepcopy(state)
    if action == "context":
        if s["draft"] or s["palletDraft"]:
            raise RuleError("Перед сменой линии или партии завершите текущий короб и паллету.")
        if data.get("line") not in ("LINE-1", "LINE-2"):
            raise RuleError("Выберите линию 1 или 2.")
        batch = str(data.get("batch", "")).strip()
        if not batch or len(batch) > 80 or any(ord(c) < 32 for c in batch):
            raise RuleError("Номер партии должен содержать от 1 до 80 печатных символов.")
        s["line"], s["batch"] = data["line"], batch
        log(s, f"Выбрана {s['line']}, партия {batch}")
    elif action == "product":
        if s["draft"] or s["palletDraft"]:
            raise RuleError("Перед сменой продукции завершите или очистите текущий короб.")
        if data.get("product") not in s["settings"]["recipes"]:
            raise RuleError("Неизвестная продукция.")
        s["product"] = data["product"]
    elif action == "scan":
        add_code(s, data.get("code", ""))
        log(s, "Считан код " + s["draft"][-1])
    elif action == "fill":
        fill(s)
        log(s, "Имитатор камеры заполнил текущий короб")
    elif action == "remove-code":
        code = data.get("code")
        if code not in s["draft"]:
            raise RuleError("Код не найден в текущем коробе.")
        s["draft"].remove(code)
        log(s, "Удалён код из незавершённого короба: " + code)
    elif action == "clear-draft":
        s["draft"] = []
        log(s, "Очищен незавершённый короб", "warning")
    elif action == "close-box":
        close_box(s)
    elif action == "add-box":
        add_box(s, data.get("id"))
        log(s, "Добавлен в паллету короб " + data["id"])
    elif action == "remove-box":
        if data.get("id") not in s["palletDraft"]:
            raise RuleError("Короб не найден в текущей паллете.")
        s["palletDraft"].remove(data["id"])
        if not s["palletDraft"]:
            s["palletTarget"] = None
        log(s, "Короб удалён из незавершённой паллеты: " + data["id"])
    elif action == "fill-pallet":
        if s["draft"]:
            raise RuleError("Сначала завершите текущий короб, чтобы сохранить его состав.")
        target = s["palletTarget"] or s["settings"]["palletSize"]
        for b in s["boxes"]:
            if len(s["palletDraft"]) >= target:
                break
            if (not b["pallet"] and b["id"] not in s["palletDraft"]
                    and all(b[k] == s[k] for k in ("product", "line", "batch"))):
                add_box(s, b["id"])
        while len(s["palletDraft"]) < target:
            fill(s)
            add_box(s, close_box(s)["id"])
        log(s, "Имитатор подготовил полный состав паллеты")
    elif action == "close-pallet":
        if not s["palletDraft"] or len(s["palletDraft"]) != s["palletTarget"]:
            raise RuleError("Для закрытия паллеты необходимо собрать полное количество коробов.")
        first = next(b for b in s["boxes"] if b["id"] == s["palletDraft"][0])
        pallet = {"id": identifier(s, "PAL"), "boxes": list(s["palletDraft"]), "created": stamp(), "prints": 0,
                  **{k: first[k] for k in ("product", "line", "batch")}}
        s["pallets"].insert(0, pallet)
        for b in s["boxes"]:
            if b["id"] in pallet["boxes"]:
                b["pallet"] = pallet["id"]
        document(s, pallet["id"], "pallet")
        s["printJobs"].insert(0, {"id": str(uuid4()), "aggregate": pallet["id"], "status": "ready", "created": stamp()})
        s["palletDraft"], s["palletTarget"] = [], None
        log(s, "Закрыта паллета " + pallet["id"])
    elif action == "online":
        s["online"] = bool(data.get("online"))
        log(s, "Имитатор связи: " + ("доступен" if s["online"] else "нет соединения"), "warning")
    elif action in ("send", "reconcile"):
        doc = next((d for d in s["documents"] if d["id"] == data.get("id")), None)
        if not doc:
            raise RuleError("Документ не найден.")
        if doc.get("provider", "simulator") != "simulator":
            raise RuleError("Результат реального документа нельзя изменять имитатором.")
        if not s["online"]:
            raise RuleError("Нет связи с имитатором. Документ сохранён в очереди.")
        if action == "reconcile":
            if doc["status"] != "unknown":
                raise RuleError("Уточнение доступно только при неопределённом результате.")
            doc["status"], doc["message"] = "accepted", "Имитатор подтвердил предыдущую отправку"
        else:
            if doc["status"] not in ("queued", "rejected"):
                raise RuleError("Повторная отправка запрещена: документ принят или результат требует уточнения.")
            outcome = data.get("outcome", "accepted")
            if outcome not in ("accepted", "rejected", "unknown"):
                raise RuleError("Неизвестный сценарий ответа.")
            if doc["kind"] == "pallet":
                pallet = next(p for p in s["pallets"] if p["id"] == doc["aggregate"])
                if any(d["aggregate"] in pallet["boxes"] and d["status"] != "accepted" for d in s["documents"]):
                    raise RuleError("Сначала обработайте документы коробов этой паллеты.")
            doc["attempts"] += 1
            doc["status"] = outcome
            doc["message"] = {"accepted": "Принят имитатором (не ГИС МТ)", "rejected": "Учебный отказ: проверка состава не пройдена", "unknown": "Тайм-аут: уточните результат перед повторной отправкой"}[outcome]
        log(s, f"{doc['id']}: {doc['message']}", "info" if doc["status"] == "accepted" else "warning")
    elif action == "send-all":
        if not s["online"]:
            raise RuleError("Нет связи с имитатором. Очередь сохранена.")
        for kind in ("box", "pallet"):
            for d in list(s["documents"]):
                if d["kind"] == kind and d["status"] == "queued":
                    if kind == "pallet":
                        pallet = next(p for p in s["pallets"] if p["id"] == d["aggregate"])
                        if any(child["aggregate"] in pallet["boxes"] and child["status"] != "accepted" for child in s["documents"]):
                            continue
                    s = apply(s, "send", {"id": d["id"], "outcome": "accepted"})
    elif action == "print-error":
        job = next((j for j in s["printJobs"] if j["aggregate"] == data.get("id")), None)
        if not job:
            raise RuleError("Задание печати не найдено.")
        job["status"] = "failed"
        log(s, "Имитатор принтера: сбой печати " + job["aggregate"] + ". Повторите с тем же идентификатором.", "error")
    elif action == "print":
        aggregate = next((a for a in s["boxes"] + s["pallets"] if a["id"] == data.get("id")), None)
        if not aggregate:
            raise RuleError("Упаковка не найдена.")
        aggregate["prints"] += 1
        job = next((j for j in s["printJobs"] if j["aggregate"] == aggregate["id"]), None)
        if job:
            job["status"] = "preview_opened"
        log(s, "Открыта печать этикетки " + aggregate["id"] + " (физическая печать не подтверждена)")
    elif action == "settings":
        if s["draft"] or s["palletDraft"]:
            raise RuleError("Завершите текущий короб и паллету перед изменением настроек.")
        values = [data.get(k) for k in ("one", "five", "pallet")]
        if any(type(v) is not int or not 1 <= v <= 100 for v in values):
            raise RuleError("Введите целые значения от 1 до 100.")
        s["settings"] = {"recipes": {"1L": values[0], "5L": values[1]}, "palletSize": values[2]}
        log(s, "Изменены рецепты упаковки")
    else:
        raise RuleError("Неизвестная операция.")
    return s
