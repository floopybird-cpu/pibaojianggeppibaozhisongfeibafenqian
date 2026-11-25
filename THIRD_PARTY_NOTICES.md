# 源码与第三方组件

本目录新增应用源码和预置格式包按 GNU AGPL-3.0-or-later 提供，完整文本见 LICENSE。发行目录同时包含完整可修改源码、固定依赖、构建脚本和包资源。

PyMuPDF 1.26.7 的已安装声明为 GNU AGPL 3.0 / Artifex 商业许可二选一。本次采用开源许可路径，应用源码随运行组件交付。其上游许可说明为 https://pymupdf.readthedocs.io/en/latest/about.html 和 https://pymupdf.io/licensing 。若后续产品需要其他许可方式，应先调整依赖或取得相应商业许可。

PySide6 / Shiboken / Qt 6.10.0 使用开源组件与动态 DLL。原组件未修改，动态组件未静态嵌入应用，随附 LGPL/GPL 文本、安装元数据和上游地址；用户可替换兼容组件并修改源码。上游对应源码入口：https://download.qt.io/archive/qt/6.10/6.10.0/single/ 和 https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=6.10.0 。Qt 的各模块与所含第三方代码保留各自条款。

Python 3.13.16 保留 PSF 与其 bundled notices；NumPy、OpenBLAS、Matplotlib 与字体、python-docx、latex2mathml、lxml、openpyxl 和所有锁定传递依赖的许可证／元数据集中在 licenses/，完整机器可读清单为 licenses/inventory.json。没有把 Windows 的 Microsoft YaHei 字体文件复制进发行目录；界面和 Word 引用当前系统字体。

0.4.0 的 Markdown 解析使用 markdown-it-py 4.0.0、mdit-py-plugins 0.5.0、mdurl 0.1.2，均保留 MIT 许可与元数据，见 licenses/。没有复制 Marp、Slidev、Pandoc 或 Quarto 的源码。

0.5.0 将已锁定的 fonttools 4.68.0 作为直接依赖，用本机已安装字体补充公式中的中文字形；FontTools 的许可与元数据已在 licenses/ 中。临时合成字体不进入发行包或文稿源文件。

新版文稿 PDF 使用 Typst 0.15.1 官方 Windows x64 CLI（Apache-2.0）。完整 LICENSE、NOTICE 与原始发行 ZIP 保留在 runtime/typst/，上游 https://github.com/typst/typst/releases/tag/v0.15.1 。官方 ZIP SHA-256 为 19ce3551153c2fe7ee9fa2f95208310c8f4d3209fedb699e0333faf8913f6736，已与官方发行 API 的 digest 核对。全部交付字节另由构建清单固定。字体由系统提供，未再分发微软字体；公式 PDF 字形使用已有 ziamath/ziafont 的独立 SVG 路径。应用源码与锁定安装脚本随包交付。
