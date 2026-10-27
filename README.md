# 센서 에뮬레이터

온실 장부의 동 목록을 따라가며, 온도·습도·광 값을 조절해 장부로 보내는 화면입니다. 동을 추가하거나 삭제하면 다음 동기화 때 센서 목록도 같이 바뀝니다.

장부 API는 [greenhouse-ledger-backend](https://github.com/ljs3536/greenhouse-ledger-backend)입니다. 받은 값은 모니터링 화면의 온실 카드에 표시됩니다.

## 실행

장부 API가 http://127.0.0.1:8080 에서 켜져 있어야 합니다. Python 3.11이 필요합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn emulator:app --host 127.0.0.1 --port 8090
```

화면은 http://127.0.0.1:8090 입니다. 장부 주소는 `LEDGER_URL`로 바꿀 수 있습니다.
