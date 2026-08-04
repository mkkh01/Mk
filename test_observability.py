import asyncio
import sys
from datetime import datetime, timezone
from monitoring.health_manager import health_manager, HealthStatus
from monitoring.heartbeat import run_heartbeat_loop

async def test_observability():
    print("Starting Observability Test...")
    
    # 1. Start heartbeat in background
    heartbeat_task = asyncio.create_task(run_heartbeat_loop(interval_seconds=2.0))
    
    # 2. Simulate component updates
    print("Simulating component updates...")
    await health_manager.update_component("Redis", HealthStatus.OK, "Connected")
    await health_manager.update_component("Supabase", HealthStatus.OK, "Connected")
    await health_manager.update_component("WebSocket", HealthStatus.WARNING, "Latency high", {"latency_ms": 500})
    
    # 3. Simulate stats
    print("Simulating stats...")
    await health_manager.increment_stat("candles_received", 10)
    await health_manager.increment_stat("analyses_executed", 5)
    await health_manager.increment_stat("signals_emitted", 1)
    
    # 4. Wait for a couple of heartbeats
    print("Waiting for heartbeats (check logs)...")
    await asyncio.sleep(5)
    
    # 5. Check overall health
    health = await health_manager.get_overall_health()
    print(f"Overall Health Status: {health['status']}")
    assert health['status'] == HealthStatus.WARNING
    assert health['stats']['candles_received'] == 10
    
    # 6. Simulate error
    print("Simulating error...")
    await health_manager.update_component("WebSocket", HealthStatus.CRITICAL, "Connection lost")
    health = await health_manager.get_overall_health()
    print(f"New Health Status: {health['status']}")
    assert health['status'] == HealthStatus.CRITICAL
    
    # Cleanup
    heartbeat_task.cancel()
    print("Test Completed Successfully!")

if __name__ == "__main__":
    asyncio.run(test_observability())
