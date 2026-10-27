import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

LEDGER_URL = os.environ.get("LEDGER_URL", "http://127.0.0.1:8080").rstrip("/")
DEFAULT_TEMPERATURE = 22.0
DEFAULT_HUMIDITY = 65
DEFAULT_LIGHT = 12000


class HouseValues(BaseModel):
    temperature: float = Field(ge=-20, le=60)
    humidity: int = Field(ge=0, le=100)
    light: int = Field(ge=0, le=200_000)


class AutoSettings(BaseModel):
    enabled: bool
    interval_seconds: int = Field(alias="intervalSeconds", ge=2, le=120)

    model_config = {"populate_by_name": True}


@dataclass
class Channel:
    name: str
    temperature: float = DEFAULT_TEMPERATURE
    humidity: int = DEFAULT_HUMIDITY
    light: int = DEFAULT_LIGHT


@dataclass
class Emulator:
    houses: dict[int, Channel] = field(default_factory=dict)
    auto_enabled: bool = False
    interval_seconds: int = 5
    last_sent_at: str | None = None
    last_error: str | None = None
    last_attempt: float = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client: httpx.AsyncClient | None = None

    def snapshot(self) -> dict:
        return {
            "ledgerUrl": LEDGER_URL,
            "auto": {"enabled": self.auto_enabled, "intervalSeconds": self.interval_seconds},
            "lastSentAt": self.last_sent_at,
            "lastError": self.last_error,
            "houses": [
                {
                    "id": house_id,
                    "name": channel.name,
                    "temperature": channel.temperature,
                    "humidity": channel.humidity,
                    "light": channel.light,
                }
                for house_id, channel in sorted(self.houses.items())
            ],
        }


state = Emulator()


async def sync_houses() -> None:
    assert state.client is not None
    try:
        response = await state.client.get(f"{LEDGER_URL}/api/greenhouses")
        response.raise_for_status()
        houses = response.json()
    except httpx.HTTPError:
        state.last_error = "장부에 연결하지 못했습니다."
        return
    if not isinstance(houses, list):
        state.last_error = "온실 목록을 읽지 못했습니다."
        return
    seen: set[int] = set()
    for house in houses:
        house_id = int(house["id"])
        seen.add(house_id)
        current = state.houses.get(house_id)
        if current is None:
            state.houses[house_id] = Channel(name=house["name"])
        else:
            current.name = house["name"]
    for house_id in list(state.houses):
        if house_id not in seen:
            del state.houses[house_id]
    if state.last_error == "장부에 연결하지 못했습니다." or state.last_error == "온실 목록을 읽지 못했습니다.":
        state.last_error = None


async def send_readings() -> None:
    assert state.client is not None
    state.last_attempt = monotonic()
    if not state.houses:
        await sync_houses()
    if not state.houses:
        state.last_error = "전송할 온실이 없습니다."
        return
    payload = {
        "readings": [
            {
                "greenhouseId": house_id,
                "temperature": round(channel.temperature, 1),
                "humidity": channel.humidity,
                "light": channel.light,
            }
            for house_id, channel in sorted(state.houses.items())
        ]
    }
    try:
        response = await state.client.post(f"{LEDGER_URL}/api/sensors", json=payload)
        if response.status_code >= 400:
            message = "센서 값을 보내지 못했습니다."
            try:
                body = response.json()
                if isinstance(body, dict) and body.get("message"):
                    message = body["message"]
            except ValueError:
                pass
            state.last_error = message
            return
        state.last_sent_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
        state.last_error = None
    except httpx.HTTPError:
        state.last_error = "장부에 연결하지 못했습니다."


async def auto_loop() -> None:
    while True:
        await asyncio.sleep(1)
        async with state.lock:
            due = state.auto_enabled and monotonic() - state.last_attempt >= state.interval_seconds
            if due:
                await sync_houses()
                await send_readings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    state.client = httpx.AsyncClient(timeout=5)
    async with state.lock:
        await sync_houses()
    task = asyncio.create_task(auto_loop())
    try:
        yield
    finally:
        task.cancel()
        await state.client.aclose()


app = FastAPI(title="센서 에뮬레이터", lifespan=lifespan)
PAGE = Path(__file__).with_name("static") / "index.html"


@app.get("/")
async def page():
    return FileResponse(PAGE)


@app.get("/api/state")
async def read_state():
    async with state.lock:
        await sync_houses()
        return state.snapshot()


@app.put("/api/houses/{house_id}")
async def update_house(house_id: int, values: HouseValues):
    async with state.lock:
        channel = state.houses.get(house_id)
        if channel is None:
            await sync_houses()
            channel = state.houses.get(house_id)
        if channel is None:
            raise HTTPException(status_code=404, detail="온실을 찾을 수 없습니다.")
        channel.temperature = round(values.temperature, 1)
        channel.humidity = values.humidity
        channel.light = values.light
        return state.snapshot()


@app.put("/api/auto")
async def update_auto(settings: AutoSettings):
    async with state.lock:
        state.auto_enabled = settings.enabled
        state.interval_seconds = settings.interval_seconds
        return state.snapshot()


@app.post("/api/send")
async def send_now():
    async with state.lock:
        await sync_houses()
        await send_readings()
        return state.snapshot()
