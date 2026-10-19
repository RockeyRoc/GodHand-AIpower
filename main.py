"""Compatibility entry; all business implementations live in main_work."""
import runpy
if __name__ == "__main__":
    runpy.run_module("main_work",run_name="__main__")
else:
    from main_work import *
