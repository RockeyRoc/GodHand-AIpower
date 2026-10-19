"""Own descendants with a Windows job rather than matching unrelated processes."""
import ctypes
from ctypes import wintypes as w

class Basic(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('active_process_limit',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
class IO(ctypes.Structure):
    _fields_=[(name,ctypes.c_ulonglong) for name in ('read_operations','write_operations','other_operations','read_bytes','write_bytes','other_bytes')]
class Extended(ctypes.Structure):
    _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]

class Job:
    def __init__(self,process):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);self.kernel=kernel
        kernel.CreateJobObjectW.restype=w.HANDLE;kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
        kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        kernel.TerminateJobObject.argtypes=[w.HANDLE,w.UINT];kernel.CloseHandle.argtypes=[w.HANDLE]
        self.handle=kernel.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=Extended();limits.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(self.handle,w.HANDLE(int(process._handle))):
            error=ctypes.WinError(ctypes.get_last_error());self.close();raise error
    def terminate(self):
        if not self.handle:return True
        return bool(self.kernel.TerminateJobObject(self.handle,1))
    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None

def resume(process):
    fn=ctypes.WinDLL('ntdll').NtResumeProcess;fn.argtypes=[w.HANDLE];fn.restype=ctypes.c_long
    if fn(w.HANDLE(int(process._handle)))!=0:raise OSError('Cannot resume owned worker')
