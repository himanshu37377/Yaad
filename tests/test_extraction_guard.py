"""Shared per-folder extraction guard (Phase 8): the manual endpoint,
background auto-learning, and background conversation learning must
never run two extractions over one folder at once (double spend,
competing sweeps)."""
import threading

from test_memory_answers import Store

import yaad.memory.auto as auto
from yaad.config import Settings


def test_guard_admits_one_holder_per_folder():
    auto.end_folder_extraction(101)
    assert auto.begin_folder_extraction(101) is True
    assert auto.begin_folder_extraction(101) is False
    assert auto.begin_folder_extraction(102) is True  # other folders unaffected
    auto.end_folder_extraction(101)
    assert auto.begin_folder_extraction(101) is True
    auto.end_folder_extraction(101)
    auto.end_folder_extraction(102)


def test_background_scheduling_respects_a_held_guard(tmp_path):
    store = Store(tmp_path / "guard.sqlite3")
    store.settings = Settings(db_path=tmp_path / "guard.sqlite3",
                              cloud_enabled=True, model_api_key="stub")
    assert auto.begin_folder_extraction(store.folder_id) is True
    try:
        started = auto.schedule_auto_memory(store.db, store.settings, [store.folder_id])
        assert started == []  # manual holder wins; no background overlap
    finally:
        auto.end_folder_extraction(store.folder_id)
    started = auto.schedule_auto_memory(store.db, store.settings, [store.folder_id])
    assert len(started) == 1
    auto.wait_auto_memory(timeout=30)
    # After the background run finished, the slot is free again and
    # finished threads are pruned instead of accumulating.
    assert auto.begin_folder_extraction(store.folder_id) is True
    auto.end_folder_extraction(store.folder_id)
    assert not any(thread.is_alive() for thread in auto._threads)


def test_guard_is_thread_safe_under_contention():
    folder_id = 777
    auto.end_folder_extraction(folder_id)
    outcomes = []

    def contender():
        outcomes.append(auto.begin_folder_extraction(folder_id))

    threads = [threading.Thread(target=contender) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count(True) == 1
    auto.end_folder_extraction(folder_id)
