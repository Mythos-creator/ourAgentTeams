"""Offline evaluation harness: benchmark suite + pairwise ELO judging.

Independent module — runs without Leader/orchestrator. Drives workers
directly via BaseModelWorker.chat().
"""
