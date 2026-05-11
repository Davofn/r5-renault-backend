import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="R5 Renault Backend")

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
APP_SHARED_SECRET = os.getenv("APP_SHARED_SECRET", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN] if ALLOWED_ORIGIN != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {
        "ok": True,
        "service": "R5 Renault Backend"
    }

@app.get("/health")
def health():
    return {
        "ok": True
    }

@app.get("/renault/status")
def renault_status(x_app_secret: str | None = Header(default=None)):
    if APP_SHARED_SECRET and x_app_secret != APP_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return {
        "soc": None,
        "rangeKm": None,
        "odometerKm": None,
        "updatedAt": None,
        "source": "placeholder"
    }
