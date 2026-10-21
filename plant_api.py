import csv
from datetime import date
import hashlib
import io
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import JSONResponse,StreamingResponse
from pydantic import BaseModel,Field,ConfigDict
import access
import plant
import repository

router=APIRouter()


def current_user(request:Request):
    user=access.user_for(request)
    if not user:raise HTTPException(401,'Войдите под учётной записью оператора.')
    return user


@router.get('/api/access/me')
def me(request:Request):return {'user':access.user_for(request)}


class Login(BaseModel):
    username:str=Field(min_length=1,max_length=80)
    password:str=Field(min_length=1,max_length=256)


@router.post('/api/access/login')
def login(body:Login,request:Request):
    token=access.login(body.username,body.password)
    if not token:raise HTTPException(401,'Неверный логин или пароль.')
    response=JSONResponse({'ok':True})
    response.set_cookie('arm_session',token,max_age=43200,httponly=True,samesite='strict',secure=request.url.scheme=='https')
    with repository.connect() as con:
        plant.event(con,{'id':body.username},'Выполнен персональный вход в АРМ')
    return response


@router.post('/api/access/logout')
def logout(request:Request):
    token=request.cookies.get('arm_session','')
    if token:
        with repository.connect() as con:con.execute('DELETE FROM user_sessions WHERE token_hash=%s',(hashlib.sha256(token.encode()).hexdigest(),))
    response=JSONResponse({'ok':True});response.delete_cookie('arm_session');return response


@router.get('/api/plant/overview')
def overview(day:date|None=None,user=Depends(current_user)):return plant.overview(day)


@router.get('/api/plant/station')
def station(user=Depends(current_user)):
    with repository.connect() as con:return plant.station_view(con)


@router.get('/api/plant/catalog')
def catalog(user=Depends(current_user)):return plant.catalog()


@router.get('/api/plant/list/{view}')
def listing(view:str,limit:int=25,cursor:str|None=None,q:str='',state:str='',line:str='',kind:str='',user=Depends(current_user)):
    return plant.listing(view,limit,cursor,q,state,line,kind)


@router.get('/api/plant/aggregate/{identifier}')
def detail(identifier:str,user=Depends(current_user)):return plant.aggregate_detail(identifier)


class PlantCommand(BaseModel):
    model_config=ConfigDict(extra='forbid')
    requestId:UUID
    revision:int=Field(ge=0)
    data:dict=Field(default_factory=dict)


@router.post('/api/plant/action/{action}')
def command(action:str,body:PlantCommand,user=Depends(current_user)):
    try:
        result=plant.execute(action,body.data,str(body.requestId),body.revision,user)
    except plant.Forbidden as exc:raise HTTPException(403,str(exc)) from None
    except repository.Conflict as exc:
        with repository.connect() as con:station=plant.station_view(con)
        return JSONResponse({'error':str(exc),'station':station},status_code=409)
    return JSONResponse(result,status_code=422 if result['error'] else 200)


@router.get('/api/plant/reports')
def reports(first:date,last:date,user=Depends(current_user)):return plant.reports(first,last)


@router.get('/api/plant/reports.csv')
def reports_csv(first:date,last:date,user=Depends(current_user)):
    report=plant.reports(first,last)
    def rows():
        output=io.StringIO();output.write('\ufeff');writer=csv.writer(output,delimiter=';')
        writer.writerow(['Дата','Линия','Продукция','Короба','Бутылки','Паллеты','Упаковки на карантине'])
        yield output.getvalue();output.seek(0);output.truncate(0)
        for row in report['items']:
            writer.writerow([row[k] for k in ('day','line_id','product_id','boxes','bottles','pallets','held')])
            yield output.getvalue();output.seek(0);output.truncate(0)
    return StreamingResponse(rows(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="production-{first}-{last}.csv"'})
