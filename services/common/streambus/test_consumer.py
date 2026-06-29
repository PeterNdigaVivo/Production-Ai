import asyncio
import pytest
import fakeredis.aioredis as fr
from common.streambus.consumer import consume, ensure_group, BacklogPolicy


@pytest.mark.asyncio
async def test_scaling():
    r = fr.FakeRedis()
    stream, group = "stream:detections:cam1", "tracking"
    await ensure_group(r, stream, group)
    for i in range(10):
        await r.xadd(stream, {"json": f"msg{i}"})
    seen = {"A": [], "B": []}

    def mk(n):
        async def h(eid, f):
            seen[n].append(f[b"json"].decode())
            return True
        return h

    stop = asyncio.Event()

    async def run(n):
        await consume(r, stream, group, mk(n), consumer=n, block_ms=50, count=1, stop=stop)

    tA = asyncio.create_task(run("A"))
    tB = asyncio.create_task(run("B"))
    await asyncio.sleep(0.6)
    stop.set()
    await asyncio.wait_for(asyncio.gather(tA, tB, return_exceptions=True), timeout=3)
    total = seen["A"] + seen["B"]
    print(f"  A={len(seen['A'])} B={len(seen['B'])} unique={len(set(total))} "
          f"overlap={len(set(seen['A']) & set(seen['B']))}")
    assert len(set(total)) == 10
    assert len(set(seen['A']) & set(seen['B'])) == 0
    print("  PASS scaling: each message exactly once, no double-processing")


@pytest.mark.asyncio
async def test_crash_recovery():
    r = fr.FakeRedis()
    stream, group = "stream:frames:cam1", "detection"
    await ensure_group(r, stream, group)
    for i in range(3):
        await r.xadd(stream, {"jpg": f"frame{i}"})

    async def failing(eid, f):
        return False  # never ack -> pending

    stop = asyncio.Event()

    async def run_fail():
        await consume(r, stream, group, failing, consumer="worker1",
                      block_ms=50, count=1, stop=stop)

    t = asyncio.create_task(run_fail())
    await asyncio.sleep(0.4)
    stop.set()
    await asyncio.wait_for(asyncio.gather(t, return_exceptions=True), timeout=3)

    recovered = []

    async def ok(eid, f):
        recovered.append(f[b"jpg"].decode())
        return True

    stop2 = asyncio.Event()

    async def run_ok():
        await consume(r, stream, group, ok, consumer="worker1",
                      block_ms=50, count=1, stop=stop2)

    t2 = asyncio.create_task(run_ok())
    await asyncio.sleep(0.4)
    stop2.set()
    await asyncio.wait_for(asyncio.gather(t2, return_exceptions=True), timeout=3)
    print(f"  recovered={sorted(set(recovered))}")
    assert set(recovered) >= {"frame0", "frame1", "frame2"}
    print("  PASS crash-recovery: in-flight messages reprocessed after restart")


@pytest.mark.asyncio
async def test_newest():
    r = fr.FakeRedis()
    stream, group = "stream:frames:cam2", "detection"
    await ensure_group(r, stream, group)
    for i in range(20):
        await r.xadd(stream, {"jpg": f"f{i}"})
    processed = []

    async def h(eid, f):
        processed.append(f[b"jpg"].decode())
        return True

    stop = asyncio.Event()

    async def run():
        await consume(r, stream, group, h, consumer="w",
                      policy=BacklogPolicy.NEWEST, block_ms=50, count=1, stop=stop)

    t = asyncio.create_task(run())
    await asyncio.sleep(0.5)
    stop.set()
    await asyncio.wait_for(asyncio.gather(t, return_exceptions=True), timeout=3)
    print(f"  processed={len(processed)}/20 newest_present={'f19' in processed}")
    assert "f19" in processed and len(processed) < 20
    print("  PASS newest: keeps freshest frame, skips stale backlog")


