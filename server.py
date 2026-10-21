"""Factory-local demo: FastAPI, PostgreSQL, sandbox True API adapter."""
from contextlib import asynccontextmanager
import logging
import base64
import secrets
from uuid import UUID
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
import psycopg
from config import ROOT, settings
import repository
from integrations.true_api import TrueApi, IntegrationError

logger = logging.getLogger("arm")


@asynccontextmanager
async def lifespan(app):
    config=settings()
    if config.arm_host not in ("127.0.0.1","localhost","::1") and not (config.arm_access_user and config.arm_access_password):
        raise RuntimeError("Для работы по сети задайте ARM_ACCESS_USER и ARM_ACCESS_PASSWORD и настройте HTTPS.")
    repository.migrate()
    app.state.true_api = TrueApi(settings())
    yield
    app.state.true_api.close()


app = FastAPI(title="Контур сборки — демо АРМ", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def local_boundary(request: Request, call_next):
    config=settings()
    authenticated=False
    if config.arm_access_password:
        try:
            scheme,value=request.headers.get("authorization","").split(" ",1)
            user,password=base64.b64decode(value,validate=True).decode("utf-8").split(":",1)
            authenticated=(scheme.lower()=="basic" and secrets.compare_digest(user.encode(),config.arm_access_user.encode())
                           and secrets.compare_digest(password.encode(),config.arm_access_password.encode()))
        except (ValueError,UnicodeError):
            pass
        if not authenticated:
            return JSONResponse({"error":"Требуется вход в демонстрационный АРМ"},status_code=401,
                                headers={"WWW-Authenticate":'Basic realm="ARM demo"'})
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"error":"Недопустимый источник запроса"},status_code=403)
        if request.headers.get("content-type","").split(";")[0] != "application/json":
            return JSONResponse({"error":"Требуется application/json"},status_code=415)
        if not authenticated and request.client and request.client.host not in ("127.0.0.1","::1","testclient"):
            return JSONResponse({"error":"Изменения в демо доступны только на локальном компьютере сервера."},status_code=403)
        length = request.headers.get("content-length","0")
        if not length.isdigit() or int(length)>2_500_000:
            return JSONResponse({"error":"Слишком большой запрос"},status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(psycopg.Error)
async def db_error(request,exc):
    logger.error("PostgreSQL operation failed: %s",type(exc).__name__)
    return JSONResponse({"error":"База данных недоступна или нарушена целостность данных. Обновите страницу."},status_code=503)


@app.exception_handler(IntegrationError)
async def integration_error(request,exc):
    repository.audit(str(exc),"warning")
    return JSONResponse({"error":str(exc)},status_code=422)


@app.get("/api/health")
def health():
    with repository.connect() as con:
        con.execute("SELECT 1")
    return {"status":"ok","version":"0.2.0","database":"postgresql"}


@app.get("/api/state")
def read_state():
    return repository.read_state()


@app.get("/api/export")
def export_state():
    return JSONResponse(repository.read_state(),headers={"Content-Disposition":'attachment; filename="arm-demo.json"'})


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    revision: int = Field(ge=0)
    data: dict = Field(default_factory=dict)


@app.post("/api/action/{action}")
def command(action:str,body:Command):
    try:
        state,error = repository.command(action,body.data,str(body.requestId),body.revision)
    except repository.Conflict as exc:
        return JSONResponse({"state":repository.read_state(),"error":str(exc)},status_code=409)
    return JSONResponse({"state":state,"error":error},status_code=422 if error else 200)


@app.get("/api/integration/status")
def integration_status(request:Request):
    return request.app.state.true_api.status()


@app.post("/api/integration/challenge")
def auth_challenge(request:Request):
    result = request.app.state.true_api.challenge()
    repository.audit("True API sandbox: получен запрос авторизации")
    return result


class AuthSignature(BaseModel):
    uuid: str = Field(min_length=1,max_length=100)
    signature: str = Field(min_length=4,max_length=2_000_000)


@app.post("/api/integration/authenticate")
def authenticate(body:AuthSignature,request:Request):
    result = request.app.state.true_api.authenticate(body.uuid,body.signature)
    repository.audit("True API sandbox: выполнена авторизация")
    return result


class CodeQuery(BaseModel):
    codes: list[str] = Field(min_length=1,max_length=100)


@app.post("/api/integration/codes")
def code_info(body:CodeQuery,request:Request):
    result = request.app.state.true_api.code_info(body.codes)
    repository.audit(f"True API sandbox: запрошены сведения о {len(body.codes)} кодах")
    return {"environment":"sandbox","result":result}


@app.get("/")
def index():
    return FileResponse(ROOT/"static"/"index.html")


@app.get("/{asset}")
def assets(asset:str):
    if asset not in ("app.js","styles.css"):
        return JSONResponse({"error":"Не найдено"},status_code=404)
    return FileResponse(ROOT/"static"/asset)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app,host=settings().arm_host,port=settings().arm_port)
