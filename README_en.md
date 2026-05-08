# A4 to A5 PDF Cut-Stack

Small Python script for turning A4 documentation into an A5 print layout.

The script creates an A4 landscape PDF with two A5 pages per sheet, prepared for
duplex cut-stack printing. After printing, cut the A4 stack in the middle and
place the right A5 stack behind the left A5 stack.

## Signatures

The script detects PDF signature fields and renders those pages before the
imposition step. This keeps visible signatures available for printing.

The rendered signature-page outputs are visually identical to the previously
manually corrected signature outputs this workflow was built for.

Important: the script preserves visible signatures for print output, but
cryptographic PDF signature validity is not preserved after
rendering/imposition. For this print-preparation goal, that is normally exactly
the desired behavior.

## Installation

Create a local Python environment and install PyMuPDF:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Poppler is recommended for best signature rendering because the script uses
`pdftoppm` when it is available on `PATH`. If Poppler is not installed, the
script falls back to PyMuPDF rendering.

## Usage

```sh
.venv/bin/python pdf_a5_cutstack_auto_signature_flatten.py input.pdf output.pdf
```

Example:

```sh
.venv/bin/python pdf_a5_cutstack_auto_signature_flatten.py manual.pdf out/manual_cutstack.pdf
```

Alternatively, pass only an output directory. The script then writes the output
PDF as `<input-name>_cutstack.pdf`:

```sh
.venv/bin/python pdf_a5_cutstack_auto_signature_flatten.py manual.pdf --outdir out
```

If file or folder names contain spaces, quote the paths:

```sh
.venv/bin/python pdf_a5_cutstack_auto_signature_flatten.py "MD11-AFM-00-001-I01 R02_JS-MD 3 RES Powered Aircraft Flight Manual_signed.pdf" --outdir "out"
```

## Printing

Print the generated PDF as before:

- Paper: A4
- Orientation: landscape
- Scale: 100% / actual size
- Duplex: enabled
- Duplex binding: usually flip on short edge
- In Acrobat: do not additionally select "Multiple" or "Booklet"

After printing, cut the full A4 stack in the middle and place the right A5 stack
behind the left A5 stack.
