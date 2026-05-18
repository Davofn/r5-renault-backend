import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import asyncio


BACKEND_URL = os.getenv("BACKEND_URL", "https://r5-renault-backend.onrender.com")
APP_SHARED_SECRET = os.getenv("APP_SHARED_SECRET", "")
TZ = ZoneInfo("Europe/Madrid")


def should_refresh_now() -> bool:
    now = datetime.now(TZ)

    # Lunes=0, martes=1, ..., viernes=4
    if now.weekday() > 4:
        return False

    minutes_now = now.hour * 60 + now.minute
    start = 8 * 60 + 15
    end = 8 * 60 + 45

    return start <= minutes_now <= end


async def main():
    now = datetime.now(TZ)
    print(f"Hora Madrid: {now.isoformat()}")

    if not APP_SHARED_SECRET:
        print("APP_SHARED_SECRET no configurado", file=sys.stderr)
        sys.exit(1)

    if not should_refresh_now():
        print("Fuera de ventana 08:15-08:45 Europe/Madrid. No hago nada.")
        return

    url = f"{BACKEND_URL.rstrip()}/renault/status?refresh=true"

    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers={"x-app-secret": APP_SHARED_SECRET},
            timeout=60,
        ) as response:
            text = await response.text()
            print(f"Status: {response.status}")
            print(text)

            if response.status >= 400:
                raise RuntimeError(f"Backend respondió {response.status}: {text}")


if __name__ == "__main__":
    asyncio.run(main())
