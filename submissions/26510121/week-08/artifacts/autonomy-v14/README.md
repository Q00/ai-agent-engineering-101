# Framework diagram

`framework.tex` is the editable LaTeX/TikZ source. `framework.pdf` is a one-page landscape A3 diagram; `framework.png` is its rendered preview. The content documents the v14 proposal in `../../AUTONOMY_V14_DESIGN.md`; it is not an implementation or a deployment record.

Solid boxes show existing functions; dashed boxes show proposed additions. Purple dashed arrows carry context/evidence, and the green dashed arrow is the validated release path. The grouped specialist box contains three logical roles. Twelve logical roles do not mean twelve continuously running model instances.

From this directory, with XeLaTeX, TikZ, fontspec and the Malgun Gothic font installed:

```powershell
xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error framework.tex
xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error framework.tex
..\..\.venv\Scripts\python.exe render_preview.py --report ..\..\logs\framework-render-your-new-run.json
```

The preview script uses the existing `pypdfium2` dependency. Use a fresh report filename to preserve prior logs. `build-02/` retains the first successful build; `build-03/` contains the final layout after moving a caption away from an arrow. The failed first output-path attempt is preserved in `../../logs/autonomy-v14-latex-build-01-20261111.txt`. Compilation succeeds when run inside the build directory rather than passing the relative output-directory argument used in that attempt.
