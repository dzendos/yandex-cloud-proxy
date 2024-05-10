import os
from datetime import datetime

import dotenv
import requests
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pytz import utc
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from starlette.middleware.sessions import SessionMiddleware

dotenv.load_dotenv('.env')
OAUTH_TOKEN = os.getenv("OAUTH_TOKEN")
FOLDER_ID = os.getenv("FOLDER_ID")
ORGANIZATION_ID = os.getenv("ORGANIZATION_ID")
TOKEN = ""

DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="some-random-string")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RequestTable(Base):
    __tablename__ = 'requests'
    id = Column(Integer, primary_key=True)
    email = Column(String, index=True)
    description = Column(String)
    time_of_request = Column(DateTime, default=datetime.utcnow)
    current_role = Column(String)
    new_role = Column(String)
    id_user = Column(String)
    status = Column(String)


Base.metadata.create_all(bind=engine)


class Request(BaseModel):
    email: str
    description: str
    current_role: str
    new_role: str
    id_user: str
    status: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/requests/")
def get_all_requests(db: SessionLocal = Depends(get_db)):
    return db.query(RequestTable).all()


@app.get("/requests/active")
def get_active_requests(db: SessionLocal = Depends(get_db)):
    return db.query(RequestTable).filter(RequestTable.status == "Active").all()


@app.get("/requests/user-request/{id_user}")
def get_active_requests(id_user: str, db: SessionLocal = Depends(get_db)):
    return db.query(RequestTable).filter(RequestTable.id_user == id_user).all()


@app.put("/requests/{request_id}")
def close_request(request_id: int, status: str, db: SessionLocal = Depends(get_db)):
    request_to_close = db.query(RequestTable).filter(RequestTable.id == request_id).first()
    if not request_to_close:
        raise HTTPException(status_code=404, detail="Request not found")

    request_to_close.status = status
    db.commit()
    db.refresh(request_to_close)
    return {"message": "Request closed"}


@app.post("/requests/add")
def create_request(request_data: Request, db: SessionLocal = Depends(get_db)):
    new_request = RequestTable(
        email=request_data.email,
        description=request_data.description,
        current_role=request_data.current_role,
        new_role=request_data.new_role,
        id_user=request_data.id_user,
        status=request_data.status
    )

    db.add(new_request)
    db.commit()
    db.refresh(new_request)

    return {"message": "Request added"}


def get_organization_users():
    headers = {"Authorization": f"Bearer {TOKEN}"}
    response = requests.get(
        f"https://organization-manager.api.cloud.yandex.net/organization-manager/v1/organizations/{ORGANIZATION_ID}/users",
        headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail=response.json())


def get_user_id(email: str):
    users = get_organization_users()
    for user in users["users"]:
        if user["subjectClaims"].get("email") and user["subjectClaims"].get("email") == email:
            return user["subjectClaims"]["sub"]
    return None


@app.get("/check-user")
def is_user_exists(user_id=Depends(get_user_id)):
    if user_id:
        return {"user_id": user_id}
    raise HTTPException(status_code=404, detail="user not found")


@app.get("/get-user-role/{user_id}")
def get_user_role(user_id: str):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    response = requests.get(
        f"https://resource-manager.api.cloud.yandex.net/resource-manager/v1/folders/{FOLDER_ID}:listAccessBindings",
        headers=headers)
    if response.status_code == 200:
        for res in response.json()["accessBindings"]:
            print(res)
            if res['subject'].get("id") and res['subject'].get("id") == user_id:
                return {"role": res['roleId']}

        raise HTTPException(status_code=404, detail="user not found")
    else:
        raise HTTPException(status_code=response.status_code, detail=response.json())


@app.post("/assign-user-role")
def assign_role_to_user(user_id: str, role: str, old_role: str):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    }

    dataRemove = {
        "accessBindingDeltas": [{
            "action": "REMOVE",
            "accessBinding": {
                "roleId": old_role,
                "subject": {
                    "id": user_id,
                    "type": "userAccount"
                }
            }
        }
        ]
    }

    requests.post(
        f"https://resource-manager.api.cloud.yandex.net/resource-manager/v1/folders/{FOLDER_ID}:updateAccessBindings",
        json=dataRemove, headers=headers)

    data = {
        "accessBindingDeltas": [{
            "action": "ADD",
            "accessBinding": {
                "roleId": role,
                "subject": {
                    "id": user_id,
                    "type": "userAccount"
                }
            }
        }]
    }
    response = requests.post(
        f"https://resource-manager.api.cloud.yandex.net/resource-manager/v1/folders/{FOLDER_ID}:updateAccessBindings",
        json=data, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail=response.json())


def get_token():
    global TOKEN

    url = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
    headers = {
        "Content-Type": "application/json",
    }
    payload = {
        "yandexPassportOauthToken": OAUTH_TOKEN
    }

    response = requests.post(url, headers=headers, json=payload)
    TOKEN = response.json().get("iamToken")


scheduler = BackgroundScheduler()
scheduler.configure(timezone=utc)
scheduler.add_job(get_token, 'interval', hours=1)
scheduler.start()


@app.on_event("startup")
async def startup_event():
    get_token()


if __name__ == '__main__':
    uvicorn.run("main:app", host="83.222.9.2", port=80, reload=True)
