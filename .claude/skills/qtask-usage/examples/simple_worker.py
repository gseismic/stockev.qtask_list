"""
Simple Worker Example

Demonstrates basic Worker usage with a single handler.
"""
import time
from qtask_list import Worker

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="demo",
    namespace="myapp",
    max_workers=2,
)


@worker.on("process")
def process_task(task):
    """Handle process tasks."""
    print(f"Processing: {task}")
    time.sleep(0.1)
    return {"status": "done"}


if __name__ == "__main__":
    worker.run()
