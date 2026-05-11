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
            if key in data:
                return data.get(key)

            data_attributes = data.get("attributes")
            if isinstance(data_attributes, dict) and key in data_attributes:
                return data_attributes.get(key)

    return getattr(payload, key, default)


def to_plain_data(payload: Any) -> Any:
    if payload is None:
        return None

    if isinstance(payload, dict):
        return payload

    if isinstance(payload, list):
        return [to_plain_data(item) for item in payload]

    if hasattr(payload, "model_dump"):
        return payload.model_dump()

    if hasattr(payload, "dict"):
        return payload.dict()

    if hasattr(payload, "__dict__"):
        return {
            key: to_plain_data(value)
            for key, value in payload.__dict__.items()
            if not key.startswith("_")
        }

    return str(payload)


def to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_plug_label(plug_status: Any) -> str | None:
    normalized = to_int_or_none(plug_status)

    labels = {
        0: "Desenchufado",
        1: "Enchufado",
    }

    if normalized in labels:
        return labels[normalized]

    return str(plug_status) if plug_status is not None else None


def get_charging_label(charging_status: Any) -> str | None:
    normalized = to_float_or_none(charging_status)

    labels = {
        0.0: "No cargando",
        1.0: "Cargando",
        -1.0: "Error",
    }

    if normalized in labels:
        return labels[normalized]

    return str(charging_status) if charging_status is not None else None


async def create_client():
    if not MYRENAULT_EMAIL or not MYRENAULT_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="Faltan MYRENAULT_EMAIL o MYRENAULT_PASSWORD en Render."
        )

    websession = aiohttp.ClientSession()
    client = RenaultClient(websession=websession, locale=MYRENAULT_LOCALE)
    await client.session.login(MYRENAULT_EMAIL, MYRENAULT_PASSWORD)

    return client, websession


async def detect_account_id(client: RenaultClient) -> str:
    if MYRENAULT_ACCOUNT_ID:
        return MYRENAULT_ACCOUNT_ID

    person = await client.get_person()
    person_plain = to_plain_data(person)

    accounts = (
        get_attr(person, "accounts")
        or get_attr(person_plain, "accounts")
        or []
    )

    if not accounts:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "No se han encontrado cuentas MyRenault.",
                "person": person_plain,
            }
        )

    first_account = accounts[0]

    account_id = (
        get_attr(first_account, "accountId")
        or get_attr(first_account, "account_id")
        or get_attr(first_account, "kamereonAccountId")
        or get_attr(first_account, "kamereon_account_id")
    )

    if not account_id:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "No se pudo detectar account_id automáticamente.",
                "person": person_plain,
            }
        )

    return account_id


async def detect_vin(account) -> str:
    if MYRENAULT_VIN:
        return MYRENAULT_VIN

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

    return vin


async def get_renault_vehicle():
    client, websession = await create_client()

    try:
        account_id = await detect_account_id(client)
        account = await client.get_api_account(account_id)

        vin = await detect_vin(account)
        vehicle = await account.get_api_vehicle(vin)

        return vehicle, account_id, vin, websession
    except Exception:
        await websession.close()
        raise


async def fetch_renault_status() -> dict[str, Any]:
    vehicle, account_id, vin, websession = await get_renault_vehicle()

    try:
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

        plug_status = (
            get_attr(battery_plain, "plugStatus")
            or get_attr(battery_plain, "plug_status")
        )

        charging_status = (
            get_attr(battery_plain, "chargingStatus")
            or get_attr(battery_plain, "charging_status")
        )

        charging_remaining_time = (
            get_attr(battery_plain, "chargingRemainingTime")
            or get_attr(battery_plain, "charging_remaining_time")
        )

        charging_remaining_time_last_update = (
            get_attr(battery_plain, "chargingRemainingTimeLastUpdateDateTime")
            or get_attr(battery_plain, "charging_remaining_time_last_update_date_time")
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

        soc_int = to_int_or_none(soc)
        range_int = to_int_or_none(range_km)
        odometer_int = to_int_or_none(odometer_km)
        plug_int = to_int_or_none(plug_status)
        charging_float = to_float_or_none(charging_status)
        charging_remaining_int = to_int_or_none(charging_remaining_time)

        return {
            "soc": soc_int,
            "rangeKm": range_int,
            "odometerKm": odometer_int,
            "plugStatus": plug_int,
            "plugLabel": get_plug_label(plug_status),
            "chargingStatus": charging_float,
            "chargingLabel": get_charging_label(charging_status),
            "chargingRemainingTime": charging_remaining_int,
            "chargingRemainingTimeLastUpdate": charging_remaining_time_last_update,
            "updatedAt": updated_at,
            "source": "myrenault",
            "accountId": account_id,
            "vin": vin,
            "raw": {
                "batteryStatus": battery_plain,
                "cockpit": cockpit_plain,
            },
        }
    finally:
        await websession.close()


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


@app.get("/debug/vehicles")
async def debug_vehicles(x_app_secret: str | None = Header(default=None)):
    require_secret(x_app_secret)

    client, websession = await create_client()

    try:
        account_id = await detect_account_id(client)
        account = await client.get_api_account(account_id)
        vehicles_response = await account.get_vehicles()

        return {
            "accountId": account_id,
            "vehicles": to_plain_data(vehicles_response),
        }
    finally:
        await websession.close()


@app.get("/debug/endpoints")
async def debug_endpoints(x_app_secret: str | None = Header(default=None)):
    require_secret(x_app_secret)

    vehicle, account_id, vin, websession = await get_renault_vehicle()

    results = {}

    endpoint_methods = [
        "get_battery_status",
        "get_cockpit",
        "get_charge_mode",
        "get_charging_settings",
        "get_charge_schedule",
        "get_location",
        "get_hvac_status",
    ]

    try:
        for method_name in endpoint_methods:
            method = getattr(vehicle, method_name, None)

            if method is None:
                results[method_name] = {
                    "available": False,
                    "error": "Método no disponible en renault-api"
                }
                continue

            try:
                response = await method()
                results[method_name] = {
                    "available": True,
                    "ok": True,
                    "data": to_plain_data(response)
                }
            except Exception as exc:
                results[method_name] = {
                    "available": True,
                    "ok": False,
                    "error": str(exc)
                }

        return {
            "accountId": account_id,
            "vin": vin,
            "results": results,
        }
    finally:
        await websession.close()


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
