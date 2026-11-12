// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
//
// Generated: dynamic CPython binding. Do not edit by hand.
#ifdef DESKTOP_APP_WITH_PLUGINS
#include <Python.h>

#include "ayu/plugins/bridge/ayu_python_loader.h"

#include <windows.h>

namespace Ayu::Plugins::Bridge {
namespace {

void *g_pythonModule = nullptr;
int g_pythonMissing = 0;

} // namespace

int PythonModuleMissing() {
	return g_pythonMissing;
}

} // namespace Ayu::Plugins::Bridge

// `__imp_<name>` is the slot the dllimport declarations in
// pybind11/Python.h already reference. Defining them here turns the
// static import into a plain variable we can fill at startup.
extern "C" {
	decltype(&PyModule_Type) __imp_PyModule_Type = nullptr;
	decltype(&PyCFunction_Type) __imp_PyCFunction_Type = nullptr;
	decltype(&PyDict_Type) __imp_PyDict_Type = nullptr;
	decltype(&PyFloat_Type) __imp_PyFloat_Type = nullptr;
	decltype(&_Py_TrueStruct) __imp__Py_TrueStruct = nullptr;
	decltype(&_Py_FalseStruct) __imp__Py_FalseStruct = nullptr;
	decltype(&PyExc_ValueError) __imp_PyExc_ValueError = nullptr;
	decltype(&PyExc_TypeError) __imp_PyExc_TypeError = nullptr;
	decltype(&PyExc_SystemError) __imp_PyExc_SystemError = nullptr;
	decltype(&PyExc_RuntimeError) __imp_PyExc_RuntimeError = nullptr;
	decltype(&PyExc_OverflowError) __imp_PyExc_OverflowError = nullptr;
	decltype(&PyExc_MemoryError) __imp_PyExc_MemoryError = nullptr;
	decltype(&PyExc_IndexError) __imp_PyExc_IndexError = nullptr;
	decltype(&PyExc_ImportError) __imp_PyExc_ImportError = nullptr;
	decltype(&PyExc_BufferError) __imp_PyExc_BufferError = nullptr;
decltype(&PyExc_AttributeError) __imp_PyExc_AttributeError = nullptr;
	decltype(&PyByteArray_Type) __imp_PyByteArray_Type = nullptr;
	decltype(&_Py_NotImplementedStruct) __imp__Py_NotImplementedStruct = nullptr;
	decltype(&_Py_NoneStruct) __imp__Py_NoneStruct = nullptr;
	decltype(&PyBaseObject_Type) __imp_PyBaseObject_Type = nullptr;
	decltype(&PyType_Type) __imp_PyType_Type = nullptr;
	decltype(&PyObject_IsInstance) __imp_PyObject_IsInstance = nullptr;
	decltype(&PySequence_Tuple) __imp_PySequence_Tuple = nullptr;
	decltype(&PySequence_GetItem) __imp_PySequence_GetItem = nullptr;
	decltype(&PySequence_Size) __imp_PySequence_Size = nullptr;
	decltype(&PySequence_Check) __imp_PySequence_Check = nullptr;
	decltype(&PyNumber_Long) __imp_PyNumber_Long = nullptr;
	decltype(&PyIndex_Check) __imp_PyIndex_Check = nullptr;
	decltype(&PyNumber_Check) __imp_PyNumber_Check = nullptr;
	decltype(&PyObject_SetItem) __imp_PyObject_SetItem = nullptr;
	decltype(&PyObject_CallFunctionObjArgs) __imp_PyObject_CallFunctionObjArgs = nullptr;
	decltype(&PyObject_CallObject) __imp_PyObject_CallObject = nullptr;
	decltype(&PyObject_Call) __imp_PyObject_Call = nullptr;
	decltype(&PyImport_AppendInittab) __imp_PyImport_AppendInittab = nullptr;
	decltype(&PyImport_ImportModule) __imp_PyImport_ImportModule = nullptr;
	decltype(&PyEval_AcquireThread) __imp_PyEval_AcquireThread = nullptr;
	decltype(&PyEval_SaveThread) __imp_PyEval_SaveThread = nullptr;
	decltype(&Py_InitializeFromConfig) __imp_Py_InitializeFromConfig = nullptr;
	decltype(&Py_IsInitialized) __imp_Py_IsInitialized = nullptr;
	decltype(&Py_Finalize) __imp_Py_Finalize = nullptr;
	decltype(&PyRun_StringFlags) __imp_PyRun_StringFlags = nullptr;
	decltype(&PyRun_SimpleStringFlags) __imp_PyRun_SimpleStringFlags = nullptr;
	decltype(&PyModule_Create2) __imp_PyModule_Create2 = nullptr;
	decltype(&PyModule_AddObject) __imp_PyModule_AddObject = nullptr;
	decltype(&PyThread_tss_get) __imp_PyThread_tss_get = nullptr;
	decltype(&PyThread_tss_set) __imp_PyThread_tss_set = nullptr;
	decltype(&PyThread_tss_delete) __imp_PyThread_tss_delete = nullptr;
	decltype(&PyThread_tss_create) __imp_PyThread_tss_create = nullptr;
	decltype(&PyWeakref_NewRef) __imp_PyWeakref_NewRef = nullptr;
	decltype(&PyMethod_Type) __imp_PyMethod_Type = nullptr;
	decltype(&PyThreadState_GetUnchecked) __imp_PyThreadState_GetUnchecked = nullptr;
	decltype(&PyGILState_GetThisThreadState) __imp_PyGILState_GetThisThreadState = nullptr;
	decltype(&PyGILState_Release) __imp_PyGILState_Release = nullptr;
	decltype(&PyGILState_Ensure) __imp_PyGILState_Ensure = nullptr;
	decltype(&PyThreadState_Get) __imp_PyThreadState_Get = nullptr;
	decltype(&PyThreadState_Clear) __imp_PyThreadState_Clear = nullptr;
	decltype(&PyThreadState_New) __imp_PyThreadState_New = nullptr;
	decltype(&PyInterpreterState_GetDict) __imp_PyInterpreterState_GetDict = nullptr;
	decltype(&PyInterpreterState_Get) __imp_PyInterpreterState_Get = nullptr;
	decltype(&PyConfig_SetBytesArgv) __imp_PyConfig_SetBytesArgv = nullptr;
	decltype(&PyConfig_Clear) __imp_PyConfig_Clear = nullptr;
	decltype(&PyConfig_InitPythonConfig) __imp_PyConfig_InitPythonConfig = nullptr;
	decltype(&PyStatus_Exception) __imp_PyStatus_Exception = nullptr;
	decltype(&PyStatus_IsError) __imp_PyStatus_IsError = nullptr;
	decltype(&PyFrame_GetBack) __imp_PyFrame_GetBack = nullptr;
	decltype(&PyFrame_GetCode) __imp_PyFrame_GetCode = nullptr;
	decltype(&PyFrame_GetLineNumber) __imp_PyFrame_GetLineNumber = nullptr;
	decltype(&PyCapsule_SetContext) __imp_PyCapsule_SetContext = nullptr;
	decltype(&PyCapsule_SetPointer) __imp_PyCapsule_SetPointer = nullptr;
	decltype(&PyCapsule_GetContext) __imp_PyCapsule_GetContext = nullptr;
	decltype(&PyMem_Calloc) __imp_PyMem_Calloc = nullptr;
	decltype(&PyCapsule_GetPointer) __imp_PyCapsule_GetPointer = nullptr;
	decltype(&PyCapsule_New) __imp_PyCapsule_New = nullptr;
	decltype(&PyInstanceMethod_New) __imp_PyInstanceMethod_New = nullptr;
	decltype(&PyCMethod_New) __imp_PyCMethod_New = nullptr;
	decltype(&PyObject_GenericGetDict) __imp_PyObject_GenericGetDict = nullptr;
	decltype(&PyDict_DelItemString) __imp_PyDict_DelItemString = nullptr;
	decltype(&PyDict_Contains) __imp_PyDict_Contains = nullptr;
	decltype(&PyDict_Copy) __imp_PyDict_Copy = nullptr;
	decltype(&PyDict_Size) __imp_PyDict_Size = nullptr;
	decltype(&PyDict_Next) __imp_PyDict_Next = nullptr;
	decltype(&PyDict_GetItemWithError) __imp_PyDict_GetItemWithError = nullptr;
	decltype(&PyDict_New) __imp_PyDict_New = nullptr;
	decltype(&PyList_Append) __imp_PyList_Append = nullptr;
	decltype(&PyList_GetItem) __imp_PyList_GetItem = nullptr;
	decltype(&PyList_Size) __imp_PyList_Size = nullptr;
	decltype(&PyList_New) __imp_PyList_New = nullptr;
	decltype(&PyTuple_SetItem) __imp_PyTuple_SetItem = nullptr;
	decltype(&PyTuple_GetItem) __imp_PyTuple_GetItem = nullptr;
	decltype(&PyTuple_Size) __imp_PyTuple_Size = nullptr;
	decltype(&PyTuple_New) __imp_PyTuple_New = nullptr;
	decltype(&PyLong_AsLongLong) __imp_PyLong_AsLongLong = nullptr;
	decltype(&PyLong_FromLongLong) __imp_PyLong_FromLongLong = nullptr;
	decltype(&PyLong_AsLong) __imp_PyLong_AsLong = nullptr;
	decltype(&PyLong_FromSsize_t) __imp_PyLong_FromSsize_t = nullptr;
	decltype(&PyErr_WriteUnraisable) __imp_PyErr_WriteUnraisable = nullptr;
	decltype(&PyErr_Format) __imp_PyErr_Format = nullptr;
	decltype(&PyInstanceMethod_Type) __imp_PyInstanceMethod_Type = nullptr;
	decltype(&PyCapsule_Type) __imp_PyCapsule_Type = nullptr;
	decltype(&PyProperty_Type) __imp_PyProperty_Type = nullptr;
	decltype(&PyThreadState_DeleteCurrent) __imp_PyThreadState_DeleteCurrent = nullptr;
	decltype(&PyCapsule_GetName) __imp_PyCapsule_GetName = nullptr;
	decltype(&PyException_SetContext) __imp_PyException_SetContext = nullptr;
	decltype(&PyException_SetCause) __imp_PyException_SetCause = nullptr;
	decltype(&PyException_SetTraceback) __imp_PyException_SetTraceback = nullptr;
	decltype(&PyErr_NormalizeException) __imp_PyErr_NormalizeException = nullptr;
	decltype(&PyErr_Restore) __imp_PyErr_Restore = nullptr;
	decltype(&PyErr_Fetch) __imp_PyErr_Fetch = nullptr;
	decltype(&PyErr_Clear) __imp_PyErr_Clear = nullptr;
	decltype(&PyErr_Occurred) __imp_PyErr_Occurred = nullptr;
	decltype(&PyErr_SetString) __imp_PyErr_SetString = nullptr;
	decltype(&PyUnicode_AsUTF8AndSize) __imp_PyUnicode_AsUTF8AndSize = nullptr;
	decltype(&PyUnicode_AsUTF8String) __imp_PyUnicode_AsUTF8String = nullptr;
	decltype(&PyUnicode_DecodeUTF8) __imp_PyUnicode_DecodeUTF8 = nullptr;
	decltype(&PyUnicode_AsEncodedString) __imp_PyUnicode_AsEncodedString = nullptr;
	decltype(&PyUnicode_FromFormat) __imp_PyUnicode_FromFormat = nullptr;
	decltype(&PyUnicode_FromString) __imp_PyUnicode_FromString = nullptr;
decltype(&PyUnicode_FromStringAndSize) __imp_PyUnicode_FromStringAndSize = nullptr;
	decltype(&PyBytes_AsStringAndSize) __imp_PyBytes_AsStringAndSize = nullptr;
	decltype(&PyBytes_AsString) __imp_PyBytes_AsString = nullptr;
	decltype(&PyBytes_Size) __imp_PyBytes_Size = nullptr;
	decltype(&PyByteArray_AsString) __imp_PyByteArray_AsString = nullptr;
	decltype(&PyByteArray_Size) __imp_PyByteArray_Size = nullptr;
	decltype(&PyObject_GC_UnTrack) __imp_PyObject_GC_UnTrack = nullptr;
	decltype(&PyObject_Malloc) __imp_PyObject_Malloc = nullptr;
	decltype(&_Py_Dealloc) __imp__Py_Dealloc = nullptr;
	decltype(&PyObject_ClearManagedDict) __imp_PyObject_ClearManagedDict = nullptr;
	decltype(&PyObject_VisitManagedDict) __imp_PyObject_VisitManagedDict = nullptr;
	decltype(&_PyObject_GetDictPtr) __imp__PyObject_GetDictPtr = nullptr;
	decltype(&_PyType_Lookup) __imp__PyType_Lookup = nullptr;
	decltype(&PyObject_ClearWeakRefs) __imp_PyObject_ClearWeakRefs = nullptr;
	decltype(&PyCallable_Check) __imp_PyCallable_Check = nullptr;
	decltype(&PyObject_GenericSetDict) __imp_PyObject_GenericSetDict = nullptr;
	decltype(&PyObject_SetAttr) __imp_PyObject_SetAttr = nullptr;
	decltype(&PyObject_HasAttrString) __imp_PyObject_HasAttrString = nullptr;
	decltype(&PyObject_SetAttrString) __imp_PyObject_SetAttrString = nullptr;
	decltype(&PyObject_GetAttrString) __imp_PyObject_GetAttrString = nullptr;
	decltype(&PyObject_Str) __imp_PyObject_Str = nullptr;
	decltype(&PyObject_Repr) __imp_PyObject_Repr = nullptr;
	decltype(&PyType_Ready) __imp_PyType_Ready = nullptr;
	decltype(&PyType_IsSubtype) __imp_PyType_IsSubtype = nullptr;
	decltype(&PyBuffer_Release) __imp_PyBuffer_Release = nullptr;
	decltype(&PyMem_Free) __imp_PyMem_Free = nullptr;
	// Instantiated only by the unsigned caster templates, which the
	// previous dump did not exercise.
	decltype(&PyLong_AsUnsignedLong) __imp_PyLong_AsUnsignedLong = nullptr;
	decltype(&PyLong_AsUnsignedLongLong) __imp_PyLong_AsUnsignedLongLong = nullptr;
	// Used by the settings bridge, which converts Python values to JSON.
	decltype(&PyFloat_AsDouble) __imp_PyFloat_AsDouble = nullptr;
	decltype(&PyNumber_Float) __imp_PyNumber_Float = nullptr;
	decltype(&PyBool_Type) __imp_PyBool_Type = nullptr;
	decltype(&PyObject_GetItem) __imp_PyObject_GetItem = nullptr;
	decltype(&PyObject_GetIter) __imp_PyObject_GetIter = nullptr;
	decltype(&PyIter_Next) __imp_PyIter_Next = nullptr;
	decltype(&PyGILState_Check) __imp_PyGILState_Check = nullptr;
}

namespace Ayu::Plugins::Bridge {
namespace {

struct Resolver {
	Resolver() {
		g_pythonModule = reinterpret_cast<void *>(
			LoadLibraryW(L"python314.dll"));
		if (!g_pythonModule) {
			g_pythonMissing = 1;
			return;
		}
		__imp_PyModule_Type = reinterpret_cast<decltype(&PyModule_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyModule_Type"));
		__imp_PyCFunction_Type = reinterpret_cast<decltype(&PyCFunction_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCFunction_Type"));
		__imp_PyDict_Type = reinterpret_cast<decltype(&PyDict_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyDict_Type"));
		__imp_PyFloat_Type = reinterpret_cast<decltype(&PyFloat_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyFloat_Type"));
		__imp__Py_TrueStruct = reinterpret_cast<decltype(&_Py_TrueStruct)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "_Py_TrueStruct"));
		__imp__Py_FalseStruct = reinterpret_cast<decltype(&_Py_FalseStruct)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "_Py_FalseStruct"));
		__imp_PyExc_ValueError = reinterpret_cast<decltype(&PyExc_ValueError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_ValueError"));
		__imp_PyExc_TypeError = reinterpret_cast<decltype(&PyExc_TypeError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_TypeError"));
		__imp_PyExc_SystemError = reinterpret_cast<decltype(&PyExc_SystemError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_SystemError"));
		__imp_PyExc_RuntimeError = reinterpret_cast<decltype(&PyExc_RuntimeError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_RuntimeError"));
		__imp_PyExc_OverflowError = reinterpret_cast<decltype(&PyExc_OverflowError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_OverflowError"));
		__imp_PyExc_MemoryError = reinterpret_cast<decltype(&PyExc_MemoryError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_MemoryError"));
		__imp_PyExc_IndexError = reinterpret_cast<decltype(&PyExc_IndexError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_IndexError"));
		__imp_PyExc_ImportError = reinterpret_cast<decltype(&PyExc_ImportError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_ImportError"));
__imp_PyExc_BufferError = reinterpret_cast<decltype(&PyExc_BufferError)>(
		GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_BufferError"));
	__imp_PyExc_AttributeError = reinterpret_cast<decltype(&PyExc_AttributeError)>(
		GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyExc_AttributeError"));
		__imp_PyByteArray_Type = reinterpret_cast<decltype(&PyByteArray_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyByteArray_Type"));
		__imp__Py_NotImplementedStruct = reinterpret_cast<decltype(&_Py_NotImplementedStruct)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "_Py_NotImplementedStruct"));
		__imp__Py_NoneStruct = reinterpret_cast<decltype(&_Py_NoneStruct)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "_Py_NoneStruct"));
		__imp_PyBaseObject_Type = reinterpret_cast<decltype(&PyBaseObject_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyBaseObject_Type"));
		__imp_PyType_Type = reinterpret_cast<decltype(&PyType_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyType_Type"));
		__imp_PyObject_IsInstance = reinterpret_cast<decltype(&PyObject_IsInstance)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_IsInstance"));
		__imp_PySequence_Tuple = reinterpret_cast<decltype(&PySequence_Tuple)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PySequence_Tuple"));
		__imp_PySequence_GetItem = reinterpret_cast<decltype(&PySequence_GetItem)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PySequence_GetItem"));
		__imp_PySequence_Size = reinterpret_cast<decltype(&PySequence_Size)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PySequence_Size"));
		__imp_PySequence_Check = reinterpret_cast<decltype(&PySequence_Check)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PySequence_Check"));
		__imp_PyNumber_Long = reinterpret_cast<decltype(&PyNumber_Long)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyNumber_Long"));
		__imp_PyIndex_Check = reinterpret_cast<decltype(&PyIndex_Check)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyIndex_Check"));
		__imp_PyNumber_Check = reinterpret_cast<decltype(&PyNumber_Check)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyNumber_Check"));
		__imp_PyObject_SetItem = reinterpret_cast<decltype(&PyObject_SetItem)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_SetItem"));
		__imp_PyObject_CallFunctionObjArgs = reinterpret_cast<decltype(&PyObject_CallFunctionObjArgs)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_CallFunctionObjArgs"));
		__imp_PyObject_CallObject = reinterpret_cast<decltype(&PyObject_CallObject)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_CallObject"));
		__imp_PyObject_Call = reinterpret_cast<decltype(&PyObject_Call)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_Call"));
		__imp_PyImport_AppendInittab = reinterpret_cast<decltype(&PyImport_AppendInittab)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyImport_AppendInittab"));
		__imp_PyImport_ImportModule = reinterpret_cast<decltype(&PyImport_ImportModule)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyImport_ImportModule"));
		__imp_PyEval_AcquireThread = reinterpret_cast<decltype(&PyEval_AcquireThread)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyEval_AcquireThread"));
		__imp_PyEval_SaveThread = reinterpret_cast<decltype(&PyEval_SaveThread)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyEval_SaveThread"));
		__imp_Py_InitializeFromConfig = reinterpret_cast<decltype(&Py_InitializeFromConfig)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "Py_InitializeFromConfig"));
		__imp_Py_IsInitialized = reinterpret_cast<decltype(&Py_IsInitialized)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "Py_IsInitialized"));
		__imp_Py_Finalize = reinterpret_cast<decltype(&Py_Finalize)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "Py_Finalize"));
		__imp_PyRun_StringFlags = reinterpret_cast<decltype(&PyRun_StringFlags)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyRun_StringFlags"));
		__imp_PyRun_SimpleStringFlags = reinterpret_cast<decltype(&PyRun_SimpleStringFlags)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyRun_SimpleStringFlags"));
		__imp_PyModule_Create2 = reinterpret_cast<decltype(&PyModule_Create2)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyModule_Create2"));
		__imp_PyModule_AddObject = reinterpret_cast<decltype(&PyModule_AddObject)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyModule_AddObject"));
		__imp_PyThread_tss_get = reinterpret_cast<decltype(&PyThread_tss_get)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyThread_tss_get"));
		__imp_PyThread_tss_set = reinterpret_cast<decltype(&PyThread_tss_set)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyThread_tss_set"));
		__imp_PyThread_tss_delete = reinterpret_cast<decltype(&PyThread_tss_delete)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyThread_tss_delete"));
		__imp_PyThread_tss_create = reinterpret_cast<decltype(&PyThread_tss_create)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyThread_tss_create"));
		__imp_PyWeakref_NewRef = reinterpret_cast<decltype(&PyWeakref_NewRef)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyWeakref_NewRef"));
		__imp_PyMethod_Type = reinterpret_cast<decltype(&PyMethod_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyMethod_Type"));
		__imp_PyThreadState_GetUnchecked = reinterpret_cast<decltype(&PyThreadState_GetUnchecked)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyThreadState_GetUnchecked"));
		__imp_PyGILState_GetThisThreadState = reinterpret_cast<decltype(&PyGILState_GetThisThreadState)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyGILState_GetThisThreadState"));
		__imp_PyGILState_Release = reinterpret_cast<decltype(&PyGILState_Release)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyGILState_Release"));
		__imp_PyGILState_Ensure = reinterpret_cast<decltype(&PyGILState_Ensure)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyGILState_Ensure"));
		__imp_PyThreadState_Get = reinterpret_cast<decltype(&PyThreadState_Get)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyThreadState_Get"));
		__imp_PyThreadState_Clear = reinterpret_cast<decltype(&PyThreadState_Clear)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyThreadState_Clear"));
		__imp_PyThreadState_New = reinterpret_cast<decltype(&PyThreadState_New)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyThreadState_New"));
		__imp_PyInterpreterState_GetDict = reinterpret_cast<decltype(&PyInterpreterState_GetDict)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyInterpreterState_GetDict"));
		__imp_PyInterpreterState_Get = reinterpret_cast<decltype(&PyInterpreterState_Get)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyInterpreterState_Get"));
		__imp_PyConfig_SetBytesArgv = reinterpret_cast<decltype(&PyConfig_SetBytesArgv)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyConfig_SetBytesArgv"));
		__imp_PyConfig_Clear = reinterpret_cast<decltype(&PyConfig_Clear)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyConfig_Clear"));
		__imp_PyConfig_InitPythonConfig = reinterpret_cast<decltype(&PyConfig_InitPythonConfig)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyConfig_InitPythonConfig"));
		__imp_PyStatus_Exception = reinterpret_cast<decltype(&PyStatus_Exception)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyStatus_Exception"));
		__imp_PyStatus_IsError = reinterpret_cast<decltype(&PyStatus_IsError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyStatus_IsError"));
		__imp_PyFrame_GetBack = reinterpret_cast<decltype(&PyFrame_GetBack)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyFrame_GetBack"));
		__imp_PyFrame_GetCode = reinterpret_cast<decltype(&PyFrame_GetCode)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyFrame_GetCode"));
		__imp_PyFrame_GetLineNumber = reinterpret_cast<decltype(&PyFrame_GetLineNumber)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyFrame_GetLineNumber"));
		__imp_PyCapsule_SetContext = reinterpret_cast<decltype(&PyCapsule_SetContext)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCapsule_SetContext"));
		__imp_PyCapsule_SetPointer = reinterpret_cast<decltype(&PyCapsule_SetPointer)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCapsule_SetPointer"));
		__imp_PyCapsule_GetContext = reinterpret_cast<decltype(&PyCapsule_GetContext)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCapsule_GetContext"));
		__imp_PyMem_Calloc = reinterpret_cast<decltype(&PyMem_Calloc)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyMem_Calloc"));
		__imp_PyCapsule_GetPointer = reinterpret_cast<decltype(&PyCapsule_GetPointer)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCapsule_GetPointer"));
		__imp_PyCapsule_New = reinterpret_cast<decltype(&PyCapsule_New)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCapsule_New"));
		__imp_PyInstanceMethod_New = reinterpret_cast<decltype(&PyInstanceMethod_New)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyInstanceMethod_New"));
		__imp_PyCMethod_New = reinterpret_cast<decltype(&PyCMethod_New)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCMethod_New"));
		__imp_PyObject_GenericGetDict = reinterpret_cast<decltype(&PyObject_GenericGetDict)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_GenericGetDict"));
		__imp_PyDict_DelItemString = reinterpret_cast<decltype(&PyDict_DelItemString)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyDict_DelItemString"));
		__imp_PyDict_Contains = reinterpret_cast<decltype(&PyDict_Contains)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyDict_Contains"));
		__imp_PyDict_Copy = reinterpret_cast<decltype(&PyDict_Copy)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyDict_Copy"));
		__imp_PyDict_Size = reinterpret_cast<decltype(&PyDict_Size)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyDict_Size"));
		__imp_PyDict_Next = reinterpret_cast<decltype(&PyDict_Next)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyDict_Next"));
		__imp_PyDict_GetItemWithError = reinterpret_cast<decltype(&PyDict_GetItemWithError)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyDict_GetItemWithError"));
		__imp_PyDict_New = reinterpret_cast<decltype(&PyDict_New)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyDict_New"));
		__imp_PyList_Append = reinterpret_cast<decltype(&PyList_Append)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyList_Append"));
		__imp_PyList_GetItem = reinterpret_cast<decltype(&PyList_GetItem)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyList_GetItem"));
		__imp_PyList_Size = reinterpret_cast<decltype(&PyList_Size)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyList_Size"));
		__imp_PyList_New = reinterpret_cast<decltype(&PyList_New)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyList_New"));
		__imp_PyTuple_SetItem = reinterpret_cast<decltype(&PyTuple_SetItem)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyTuple_SetItem"));
		__imp_PyTuple_GetItem = reinterpret_cast<decltype(&PyTuple_GetItem)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyTuple_GetItem"));
		__imp_PyTuple_Size = reinterpret_cast<decltype(&PyTuple_Size)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyTuple_Size"));
		__imp_PyTuple_New = reinterpret_cast<decltype(&PyTuple_New)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyTuple_New"));
		__imp_PyLong_AsLongLong = reinterpret_cast<decltype(&PyLong_AsLongLong)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyLong_AsLongLong"));
		__imp_PyLong_FromLongLong = reinterpret_cast<decltype(&PyLong_FromLongLong)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyLong_FromLongLong"));
		__imp_PyLong_AsLong = reinterpret_cast<decltype(&PyLong_AsLong)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyLong_AsLong"));
		__imp_PyLong_FromSsize_t = reinterpret_cast<decltype(&PyLong_FromSsize_t)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyLong_FromSsize_t"));
		__imp_PyErr_WriteUnraisable = reinterpret_cast<decltype(&PyErr_WriteUnraisable)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyErr_WriteUnraisable"));
		__imp_PyErr_Format = reinterpret_cast<decltype(&PyErr_Format)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyErr_Format"));
		__imp_PyInstanceMethod_Type = reinterpret_cast<decltype(&PyInstanceMethod_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyInstanceMethod_Type"));
		__imp_PyCapsule_Type = reinterpret_cast<decltype(&PyCapsule_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCapsule_Type"));
		__imp_PyProperty_Type = reinterpret_cast<decltype(&PyProperty_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyProperty_Type"));
		__imp_PyThreadState_DeleteCurrent = reinterpret_cast<decltype(&PyThreadState_DeleteCurrent)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyThreadState_DeleteCurrent"));
		__imp_PyCapsule_GetName = reinterpret_cast<decltype(&PyCapsule_GetName)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCapsule_GetName"));
		__imp_PyException_SetContext = reinterpret_cast<decltype(&PyException_SetContext)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyException_SetContext"));
		__imp_PyException_SetCause = reinterpret_cast<decltype(&PyException_SetCause)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyException_SetCause"));
		__imp_PyException_SetTraceback = reinterpret_cast<decltype(&PyException_SetTraceback)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyException_SetTraceback"));
		__imp_PyErr_NormalizeException = reinterpret_cast<decltype(&PyErr_NormalizeException)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyErr_NormalizeException"));
		__imp_PyErr_Restore = reinterpret_cast<decltype(&PyErr_Restore)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyErr_Restore"));
		__imp_PyErr_Fetch = reinterpret_cast<decltype(&PyErr_Fetch)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyErr_Fetch"));
		__imp_PyErr_Clear = reinterpret_cast<decltype(&PyErr_Clear)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyErr_Clear"));
		__imp_PyErr_Occurred = reinterpret_cast<decltype(&PyErr_Occurred)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyErr_Occurred"));
		__imp_PyErr_SetString = reinterpret_cast<decltype(&PyErr_SetString)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyErr_SetString"));
		__imp_PyUnicode_AsUTF8AndSize = reinterpret_cast<decltype(&PyUnicode_AsUTF8AndSize)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyUnicode_AsUTF8AndSize"));
		__imp_PyUnicode_AsUTF8String = reinterpret_cast<decltype(&PyUnicode_AsUTF8String)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyUnicode_AsUTF8String"));
		__imp_PyUnicode_DecodeUTF8 = reinterpret_cast<decltype(&PyUnicode_DecodeUTF8)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyUnicode_DecodeUTF8"));
		__imp_PyUnicode_AsEncodedString = reinterpret_cast<decltype(&PyUnicode_AsEncodedString)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyUnicode_AsEncodedString"));
		__imp_PyUnicode_FromFormat = reinterpret_cast<decltype(&PyUnicode_FromFormat)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyUnicode_FromFormat"));
__imp_PyUnicode_FromString = reinterpret_cast<decltype(&PyUnicode_FromString)>(
		GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyUnicode_FromString"));
	__imp_PyUnicode_FromStringAndSize = reinterpret_cast<decltype(&PyUnicode_FromStringAndSize)>(
		GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyUnicode_FromStringAndSize"));
		__imp_PyBytes_AsStringAndSize = reinterpret_cast<decltype(&PyBytes_AsStringAndSize)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyBytes_AsStringAndSize"));
		__imp_PyBytes_AsString = reinterpret_cast<decltype(&PyBytes_AsString)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyBytes_AsString"));
		__imp_PyBytes_Size = reinterpret_cast<decltype(&PyBytes_Size)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyBytes_Size"));
		__imp_PyByteArray_AsString = reinterpret_cast<decltype(&PyByteArray_AsString)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyByteArray_AsString"));
		__imp_PyByteArray_Size = reinterpret_cast<decltype(&PyByteArray_Size)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyByteArray_Size"));
		__imp_PyObject_GC_UnTrack = reinterpret_cast<decltype(&PyObject_GC_UnTrack)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_GC_UnTrack"));
		__imp_PyObject_Malloc = reinterpret_cast<decltype(&PyObject_Malloc)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_Malloc"));
		__imp__Py_Dealloc = reinterpret_cast<decltype(&_Py_Dealloc)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "_Py_Dealloc"));
		__imp_PyObject_ClearManagedDict = reinterpret_cast<decltype(&PyObject_ClearManagedDict)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_ClearManagedDict"));
		__imp_PyObject_VisitManagedDict = reinterpret_cast<decltype(&PyObject_VisitManagedDict)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_VisitManagedDict"));
		__imp__PyObject_GetDictPtr = reinterpret_cast<decltype(&_PyObject_GetDictPtr)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "_PyObject_GetDictPtr"));
		__imp__PyType_Lookup = reinterpret_cast<decltype(&_PyType_Lookup)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "_PyType_Lookup"));
		__imp_PyObject_ClearWeakRefs = reinterpret_cast<decltype(&PyObject_ClearWeakRefs)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_ClearWeakRefs"));
		__imp_PyCallable_Check = reinterpret_cast<decltype(&PyCallable_Check)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyCallable_Check"));
		__imp_PyObject_GenericSetDict = reinterpret_cast<decltype(&PyObject_GenericSetDict)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_GenericSetDict"));
		__imp_PyObject_SetAttr = reinterpret_cast<decltype(&PyObject_SetAttr)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_SetAttr"));
		__imp_PyObject_HasAttrString = reinterpret_cast<decltype(&PyObject_HasAttrString)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_HasAttrString"));
		__imp_PyObject_SetAttrString = reinterpret_cast<decltype(&PyObject_SetAttrString)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_SetAttrString"));
		__imp_PyObject_GetAttrString = reinterpret_cast<decltype(&PyObject_GetAttrString)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_GetAttrString"));
		__imp_PyObject_Str = reinterpret_cast<decltype(&PyObject_Str)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_Str"));
		__imp_PyObject_Repr = reinterpret_cast<decltype(&PyObject_Repr)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_Repr"));
		__imp_PyType_Ready = reinterpret_cast<decltype(&PyType_Ready)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyType_Ready"));
		__imp_PyType_IsSubtype = reinterpret_cast<decltype(&PyType_IsSubtype)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyType_IsSubtype"));
		__imp_PyBuffer_Release = reinterpret_cast<decltype(&PyBuffer_Release)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyBuffer_Release"));
		__imp_PyMem_Free = reinterpret_cast<decltype(&PyMem_Free)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyMem_Free"));
		__imp_PyLong_AsUnsignedLong = reinterpret_cast<decltype(&PyLong_AsUnsignedLong)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyLong_AsUnsignedLong"));
		__imp_PyLong_AsUnsignedLongLong = reinterpret_cast<decltype(&PyLong_AsUnsignedLongLong)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyLong_AsUnsignedLongLong"));
		__imp_PyFloat_AsDouble = reinterpret_cast<decltype(&PyFloat_AsDouble)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyFloat_AsDouble"));
		__imp_PyNumber_Float = reinterpret_cast<decltype(&PyNumber_Float)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyNumber_Float"));
		__imp_PyBool_Type = reinterpret_cast<decltype(&PyBool_Type)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyBool_Type"));
		__imp_PyObject_GetItem = reinterpret_cast<decltype(&PyObject_GetItem)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_GetItem"));
		__imp_PyObject_GetIter = reinterpret_cast<decltype(&PyObject_GetIter)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyObject_GetIter"));
		__imp_PyIter_Next = reinterpret_cast<decltype(&PyIter_Next)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyIter_Next"));
		__imp_PyGILState_Check = reinterpret_cast<decltype(&PyGILState_Check)>(
			GetProcAddress(static_cast<HMODULE>(g_pythonModule), "PyGILState_Check"));
	}
};

} // namespace

// init_seg(lib) runs before any other static initialiser, so the
// `__imp_` slots are valid before pybind11's module registration and
// before InitPython() constructs the interpreter.
#pragma init_seg(lib)
static Ayu::Plugins::Bridge::Resolver g_pythonResolver;

} // namespace Ayu::Plugins::Bridge

#endif // DESKTOP_APP_WITH_PLUGINS
