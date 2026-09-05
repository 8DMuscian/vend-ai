"""Conftest for Phase 1 tests - adds backend directory to Python path."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))