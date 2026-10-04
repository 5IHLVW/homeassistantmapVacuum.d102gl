"""Petites fenêtres Tkinter : configuration, captcha et code de vérification.

Tkinter est fourni avec Python sous Windows et macOS. Si le module est absent,
``available()`` renvoie False et le programme utilise le terminal à la place.
"""

from __future__ import annotations

import base64
import io
import webbrowser
from typing import Any

SERVER_CHOICES: list[tuple[str, str]] = [
    ("de", "Europe (de)"),
    ("cn", "Chine (cn)"),
    ("us", "États-Unis (us)"),
    ("ru", "Russie (ru)"),
    ("tw", "Taïwan (tw)"),
    ("sg", "Singapour (sg)"),
    ("in", "Inde (in)"),
    ("i2", "Inde 2 (i2)"),
    ("", "Automatique (tous les serveurs)"),
]

THEME_CHOICES: dict[str, str] = {
    "clair": "Clair : fond blanc, pièces pastel",
    "contraste": "Contraste : fond blanc, couleurs franches",
    "original": "Original : fond bleu",
}

TITLE = "Xiaomi Vacuum Map"


def available() -> bool:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def _center(window: Any) -> None:
    window.update_idletasks()
    width, height = window.winfo_width(), window.winfo_height()
    x = (window.winfo_screenwidth() - width) // 2
    y = (window.winfo_screenheight() - height) // 3
    window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    window.lift()
    window.attributes("-topmost", True)
    window.after(200, lambda: window.attributes("-topmost", False))
    window.focus_force()


def ask_settings(current: dict[str, str] | None = None) -> dict[str, str] | None:
    """Fenêtre de configuration. Renvoie les valeurs pour le fichier .env, ou None si annulé."""
    import tkinter as tk
    from tkinter import colorchooser, messagebox, ttk

    current = current or {}
    root = tk.Tk()
    root.title(f"{TITLE} - Configuration")
    root.resizable(False, False)
    result: dict[str, str] | None = None

    frame = ttk.Frame(root, padding=16)
    frame.grid(sticky="nsew")
    frame.columnconfigure(1, weight=1)

    def section(row: int, text: str) -> None:
        ttk.Label(frame, text=text, font=("", 10, "bold")).grid(column=0, row=row, columnspan=2, sticky="w", pady=(10, 4))

    def field(row: int, label: str, variable: tk.Variable, show: str | None = None) -> ttk.Entry:
        ttk.Label(frame, text=label).grid(column=0, row=row, sticky="w", padx=(0, 10), pady=3)
        entry = ttk.Entry(frame, textvariable=variable, width=42, show=show or "")
        entry.grid(column=1, row=row, sticky="ew", pady=3)
        return entry

    username = tk.StringVar(value=current.get("XIAOMI_USERNAME", ""))
    password = tk.StringVar(value=current.get("XIAOMI_PASSWORD", ""))
    token = tk.StringVar(value=current.get("VACUUM_TOKEN", ""))
    host = tk.StringVar(value=current.get("VACUUM_HOST", ""))
    refresh = tk.StringVar(value=current.get("MAP_REFRESH_SECONDS", "30"))
    scale = tk.StringVar(value=current.get("MAP_SCALE", "3"))
    theme_values = list(THEME_CHOICES)
    theme_labels = [THEME_CHOICES[v] for v in theme_values]
    current_theme = (current.get("MAP_THEME") or "clair").lower()
    theme = tk.StringVar(value=THEME_CHOICES.get(current_theme, theme_labels[0]))
    background = tk.StringVar(value=current.get("MAP_BACKGROUND", ""))

    server_labels = [label for _, label in SERVER_CHOICES]
    server_values = [value for value, _ in SERVER_CHOICES]
    current_server = (current.get("XIAOMI_SERVER") or "").lower()
    if current_server == "auto":
        current_server = ""
    server_index = server_values.index(current_server) if current_server in server_values else 0
    server = tk.StringVar(value=server_labels[server_index])

    row = 0
    ttk.Label(
        frame,
        text="La carte est stockée dans le cloud Xiaomi : le compte Mi Home est obligatoire.",
        wraplength=420,
    ).grid(column=0, row=row, columnspan=2, sticky="w")
    row += 1
    section(row, "Compte Mi Home")
    row += 1
    first = field(row, "Identifiant (e-mail, téléphone ou ID)", username)
    row += 1
    field(row, "Mot de passe", password, show="•")
    row += 1
    ttk.Label(frame, text="Serveur").grid(column=0, row=row, sticky="w", padx=(0, 10), pady=3)
    ttk.Combobox(frame, textvariable=server, values=server_labels, state="readonly", width=40).grid(column=1, row=row, sticky="ew", pady=3)
    row += 1
    section(row, "Robot (facultatif : détecté automatiquement s'il n'y en a qu'un)")
    row += 1
    field(row, "Token (32 caractères)", token)
    row += 1
    field(row, "Adresse IP locale", host)
    row += 1
    section(row, "Affichage")
    row += 1
    ttk.Label(frame, text="Rafraîchissement (secondes)").grid(column=0, row=row, sticky="w", padx=(0, 10), pady=3)
    ttk.Spinbox(frame, textvariable=refresh, from_=5, to=3600, increment=5, width=10).grid(column=1, row=row, sticky="w", pady=3)
    row += 1
    ttk.Label(frame, text="Échelle de l'image").grid(column=0, row=row, sticky="w", padx=(0, 10), pady=3)
    ttk.Spinbox(frame, textvariable=scale, from_=1, to=10, increment=0.5, width=10).grid(column=1, row=row, sticky="w", pady=3)
    row += 1
    ttk.Label(frame, text="Couleurs de la carte").grid(column=0, row=row, sticky="w", padx=(0, 10), pady=3)
    ttk.Combobox(frame, textvariable=theme, values=theme_labels, state="readonly", width=40).grid(column=1, row=row, sticky="ew", pady=3)
    row += 1
    ttk.Label(frame, text="Couleur du fond (vide = blanc)").grid(column=0, row=row, sticky="w", padx=(0, 10), pady=3)
    background_row = ttk.Frame(frame)
    background_row.grid(column=1, row=row, sticky="ew", pady=3)
    ttk.Entry(background_row, textvariable=background, width=12).pack(side="left")

    def pick_background() -> None:
        chosen = colorchooser.askcolor(color=background.get() or "#ffffff", parent=root, title="Couleur du fond")
        if chosen and chosen[1]:
            background.set(chosen[1].upper())

    ttk.Button(background_row, text="Choisir…", command=pick_background).pack(side="left", padx=(6, 0))
    row += 1

    def on_save(*_: Any) -> None:
        nonlocal result
        user_value = username.get().strip()
        token_value = token.get().strip().lower()
        if not user_value or not password.get():
            messagebox.showerror(TITLE, "L'identifiant et le mot de passe Mi Home sont obligatoires.", parent=root)
            return
        if token_value and (len(token_value) != 32 or any(c not in "0123456789abcdef" for c in token_value)):
            messagebox.showerror(TITLE, "Le token doit contenir exactement 32 caractères hexadécimaux (ou rester vide).", parent=root)
            return
        try:
            refresh_value = max(5, int(float(refresh.get())))
            scale_value = float(scale.get())
        except ValueError:
            messagebox.showerror(TITLE, "Le rafraîchissement et l'échelle doivent être des nombres.", parent=root)
            return
        result = {
            "XIAOMI_USERNAME": user_value,
            "XIAOMI_PASSWORD": password.get(),
            "XIAOMI_SERVER": server_values[server_labels.index(server.get())],
            "VACUUM_TOKEN": token_value,
            "VACUUM_HOST": host.get().strip(),
            "MAP_REFRESH_SECONDS": str(refresh_value),
            "MAP_SCALE": f"{scale_value:g}",
            "MAP_THEME": theme_values[theme_labels.index(theme.get())],
            "MAP_BACKGROUND": background.get().strip(),
        }
        root.destroy()

    def on_cancel(*_: Any) -> None:
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(column=0, row=row, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(buttons, text="Annuler", command=on_cancel).grid(column=0, row=0, padx=(0, 8))
    ttk.Button(buttons, text="Enregistrer et démarrer", command=on_save).grid(column=1, row=0)
    root.bind("<Return>", on_save)
    root.bind("<Escape>", on_cancel)
    root.protocol("WM_DELETE_WINDOW", on_cancel)
    first.focus_set()
    _center(root)
    root.mainloop()
    return result


def _png_base64(image: bytes) -> str | None:
    """Convertit n'importe quelle image en PNG base64 (Tk ne lit que PNG et GIF)."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image)) as img:
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


def _ask_code(title: str, message: str, image: bytes | None = None, link: str | None = None) -> str | None:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    result: str | None = None

    frame = ttk.Frame(root, padding=16)
    frame.grid()
    ttk.Label(frame, text=message, wraplength=420, justify="left").grid(column=0, row=0, sticky="w")

    photo = None
    if image is not None:
        data = _png_base64(image)
        if data is not None:
            photo = tk.PhotoImage(data=data)
            if photo.width() < 240:
                photo = photo.zoom(2, 2)
            ttk.Label(frame, image=photo).grid(column=0, row=1, pady=10)

    code = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=code, width=30, font=("", 12))
    entry.grid(column=0, row=2, pady=(6, 10))

    def on_ok(*_: Any) -> None:
        nonlocal result
        if code.get().strip():
            result = code.get().strip()
            root.destroy()

    def on_cancel(*_: Any) -> None:
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(column=0, row=3, sticky="e")
    if link:
        ttk.Button(buttons, text="Ouvrir le lien", command=lambda: webbrowser.open(link)).grid(column=0, row=0, padx=(0, 8))
    ttk.Button(buttons, text="Annuler", command=on_cancel).grid(column=1, row=0, padx=(0, 8))
    ttk.Button(buttons, text="Valider", command=on_ok).grid(column=2, row=0)
    root.bind("<Return>", on_ok)
    root.bind("<Escape>", on_cancel)
    root.protocol("WM_DELETE_WINDOW", on_cancel)
    entry.focus_set()
    _center(root)
    root.mainloop()
    return result


def ask_captcha(image: bytes, invalid: bool = False) -> str | None:
    message = "Xiaomi demande un captcha. Recopiez les caractères de l'image (attention aux majuscules)."
    if invalid:
        message = "Captcha incorrect. " + message
    return _ask_code(f"{TITLE} - Captcha", message, image=image)


def ask_two_factor(url: str) -> str | None:
    message = (
        "Xiaomi demande une vérification en deux étapes : un code vient d'être envoyé à l'adresse e-mail du compte.\n\n"
        "Si vous ne recevez rien, cliquez sur « Ouvrir le lien », validez la vérification dans le navigateur, "
        "puis relancez le programme."
    )
    return _ask_code(f"{TITLE} - Vérification", message, link=url)


def show_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(TITLE, message)
        root.destroy()
    except Exception:  # noqa: BLE001
        pass
