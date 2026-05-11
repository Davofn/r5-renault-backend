import os
import time
from typing import Any

import aiohttp
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from renault_api.renault_client import RenaultClient

app = FastAPI(title="R5 Renault Backend")

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
APP_SHARED_SECRET = os.getenv("APP_SHARED_SECRET", "")

MYRENAULT_EMAIL = os.getenv("MYRENAULT_EMAIL", "")
MYRENAULT_PASSWORD = os.getenv("MYRENAULT_PASSWORD", "")
MYRENAULT_LOCALE = os.getenv("MYRENAULT_LOCALE", "es_ES")
MYRENAULT_ACCOUNT_ID = os.getenv("MYRENAULT_ACCOUNT_ID", "")
MYRENAULT_VIN = os.getenv("MYRENAULT_VIN", "")

CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "600"))

_status_cache: dict[str, Any] = {
    "timestamp": 0,
    "data": None,
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN] if ALLOWED_ORIGIN != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def require_secret(x_app_secret: str | None) -> None:
    if APP_SHARED_SECRET and x_app_secret != APP_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_attr(payload: Any, key: str, default: Any = None) -> Any:
    """
    Soporta respuestas tipo dict y objetos pydantic/dataclass.
    """
    if payload is None:
      return default

    if isinstance(payload, dict):
        if key in payload:
            return payload.get(key)

        attributes = payload.get("attributes")
        if isinstance(attributes, dict) and key in attributes:
            return attributes.get(key)

        data = payload.get("data")
        if isinstance(data, dict):
            data_attributes = data.get("attributes")
            if isinstance(data_attributes, dict) and key in data_attributes:
                return data_attributes.get(key)

    return getattr(payload, key, default)


def to_plain_data(payload: Any) -> Any:
    """
    Convierte objetos de renault-api a algo serializable si es posible.
    """
    if payload is None:
        return None

    if isinstance(payload, dict):
        return payload

    if hasattr(payload, "model_dump"):
        return payload.model_dump()

    if hasattr(payload, "dict"):
        return payload.dict()

    if hasattr(payload, "__dict__"):
        return payload.__dict__

    return str(payload)


async def get_renault_vehicle():
    if not MYRENAULT_EMAIL or not MYRENAULT_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="Faltan MYRENAULT_EMAIL o MYRENAULT_PASSWORD en Render."
        )

    async with aiohttp.ClientSession() as websession:
        client = RenaultClient(websession=websession, locale=MYRENAULT_LOCALE)
        await client.session.login(MYRENAULT_EMAIL, MYRENAULT_PASSWORD)

        account_id = MYRENAULT_ACCOUNT_ID
        vin = MYRENAULT_VIN

        if not account_id:
            person = await client.get_person()
            accounts = get_attr(person, "accounts", [])

            if not accounts:
                raise HTTPException(
                    status_code=500,
                    detail="No se han encontrado cuentas MyRenault."
                )

            first_account = accounts[0]
            account_id = (
                get_attr(first_account, "accountId")
                or get_attr(first_account, "account_id")
                or get_attr(first_account, "kamereonAccountId")
            )

            if not account_id:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "message": "No se pudo detectar account_id automáticamente.",
                        "person": to_plain_data(person),
                    }
                )

        account = await client.get_api_account(account_id)

        if not vin:
    vehicles_response = await account.get_vehicles()
    vehicles_plain = to_plain_data(vehicles_response)

    vehicle_links = (
        get_attr(vehicles_response, "vehicleLinks")
        or get_attr(vehicles_response, "vehicle_links")
        or get_attr(vehicles_plain, "vehicleLinks")
        or get_attr(vehicles_plain, "vehicle_links")
        or []
    )

    if not vehicle_links:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "No se han encontrado vehículos en la cuenta MyRenault.",
                "vehicles": vehicles_plain,
            }
        )

    first_vehicle = vehicle_links[0]

    vehicle_details = (
        get_attr(first_vehicle, "vehicleDetails")
        or get_attr(first_vehicle, "vehicle_details")
        or {}
    )

    vin = (
        get_attr(first_vehicle, "vin")
        or get_attr(first_vehicle, "vehicleId")
        or get_attr(first_vehicle, "vehicle_id")
        or get_attr(vehicle_details, "vin")
    )

    if not vin:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "No se pudo detectar VIN automáticamente.",
                "vehicles": vehicles_plain,
            }
        )

        vehicle = await account.get_api_vehicle(vin)
        return vehicle, account_id, vin


async def fetch_renault_status() -> dict[str, Any]:
    vehicle, account_id, vin = await get_renault_vehicle()

    battery_status = None
    cockpit = None

    try:
        battery_status = await vehicle.get_battery_status()
    except Exception as exc:
        battery_status = {
            "error": f"No se pudo leer battery_status: {exc}"
        }

    try:
        cockpit = await vehicle.get_cockpit()
    except Exception as exc:
        cockpit = {
            "error": f"No se pudo leer cockpit: {exc}"
        }

    battery_plain = to_plain_data(battery_status)
    cockpit_plain = to_plain_data(cockpit)

    soc = (
        get_attr(battery_plain, "batteryLevel")
        or get_attr(battery_plain, "battery_level")
    )

    range_km = (
        get_attr(battery_plain, "batteryAutonomy")
        or get_attr(battery_plain, "battery_autonomy")
    )

    updated_at = (
        get_attr(battery_plain, "timestamp")
        or get_attr(cockpit_plain, "timestamp")
    )

    odometer_km = (
        get_attr(cockpit_plain, "totalMileage")
        or get_attr(cockpit_plain, "total_mileage")
        or get_attr(cockpit_plain, "mileage")
        or get_attr(cockpit_plain, "odometer")
    )

    return {
        "soc": soc,
        "rangeKm": range_km,
        "odometerKm": odometer_km,
        "updatedAt": updated_at,
        "source": "myrenault",
        "accountId": account_id,
        "vin": vin,
        "raw": {
            "batteryStatus": battery_plain,
            "cockpit": cockpit_plain,
        },
    }


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
async def renault_status(
    x_app_secret: str | None = Header(default=None),
    refresh: bool = False,
):
    require_secret(x_app_secret)

    now = time.time()

    if (
        not refresh
        and _status_cache["data"] is not None
        and now - _status_cache["timestamp"] < CACHE_SECONDS
    ):
        cached_data = dict(_status_cache["data"])
        cached_data["cached"] = True
        return cached_data

    try:
        data = await fetch_renault_status()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error consultando MyRenault: {exc}"
        )

    data["cached"] = False
    _status_cache["timestamp"] = now
    _status_cache["data"] = data

    return data
