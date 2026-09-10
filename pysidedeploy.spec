[app]
title = PDF2AI
project_dir = .
input_file = app.py
exec_directory = dist
project_file = 
icon = 

[python]
python_path = 
packages = Nuitka==4.2.1

[qt]
qml_files = 
excluded_qml_plugins = 
modules = Core,Gui,Widgets
plugins = 

[nuitka]
mode = standalone
extra_args = --assume-yes-for-downloads --noinclude-qt-translations --include-package=pdf2ai --include-package=pymupdf4llm --include-package=rapidocr --include-package-data=rapidocr --include-package-data=pymupdf --include-package-data=pymupdf4llm --include-package-data=onnxruntime --nofollow-import-to=rapidocr.inference_engine.pytorch,rapidocr.inference_engine.paddle,rapidocr.inference_engine.openvino,rapidocr.inference_engine.tensorrt,rapidocr.inference_engine.mnn --output-filename=PDF2AI
macos.permissions = 

