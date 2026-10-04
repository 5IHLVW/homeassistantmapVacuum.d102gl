# Application autonome (sans Home Assistant)

Application Python qui récupère la carte de votre aspirateur robot Xiaomi via le cloud Xiaomi (Mi Home) et l'affiche dans une petite interface web.

Elle utilise le même moteur que l'intégration Home Assistant (`custom_components/map_vacuum_d102gl/core`) : gardez ce dossier `standalone` à l'intérieur du dépôt.

Constructeurs pris en charge (détection automatique d'après le modèle) : Roborock / Mi Robot (`rockrobo.*`, `roborock.*`), Dreame (`dreame.vacuum.*`), Viomi, Roidmi, Ijai et les modèles `xiaomi.vacuum.*` récents.

## Installation

```powershell
git clone https://github.com/5IHLVW/homeassistantmapVacuum.d102gl.git
cd homeassistantmapVacuum.d102gl\standalone
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Sous Linux / macOS : `source .venv/bin/activate`.

## Configuration

Au premier lancement de `python run.py`, une fenêtre demande le compte Mi Home, le serveur et, si besoin, le token et l'IP du robot. Les valeurs sont enregistrées dans `.env`. Pour la rouvrir : `python run.py --setup`. Le captcha et le code de vérification Xiaomi sont aussi demandés dans une fenêtre.

Sans fenêtres (`--no-gui`, ou si Tkinter est absent), copiez le modèle et éditez-le :

```powershell
copy .env.example .env
```

Variables du fichier `.env` :

| Variable | Rôle |
| --- | --- |
| `XIAOMI_USERNAME`, `XIAOMI_PASSWORD` | Compte Mi Home (obligatoire : la carte est téléchargée depuis le cloud Xiaomi). |
| `XIAOMI_SERVER` | Serveur du compte : `de` pour l'Europe, `cn`, `us`, `ru`, `tw`, `sg`, `in`, `i2`. Vide = tous les serveurs. |
| `VACUUM_TOKEN` | Token de l'appareil (32 caractères hexa). Identifie le robot et permet l'accès local. |
| `VACUUM_DEVICE_ID` | Alternative au token pour choisir le robot. |
| `VACUUM_HOST` | IP locale du robot (facultatif). |
| `VACUUM_API` | `auto` (recommandé) ou `roborock`, `dreame`, `viomi`, `roidmi`, `ijai`, `xiaomi`. |
| `MAP_REFRESH_SECONDS` | Intervalle de rafraîchissement de la carte. |
| `MAP_SCALE`, `MAP_ROTATE` | Échelle et rotation de l'image générée. |
| `MAP_THEME` | Couleurs : `clair` (fond blanc, pièces pastel, par défaut), `contraste` (fond blanc, couleurs franches), `original` (fond bleu). |
| `MAP_BACKGROUND` | Couleur du fond (`#RRGGBB`), blanc si vide. |
| `MAP_ROOM_COLORS` | Couleurs des pièces séparées par des espaces, dans l'ordre des numéros de pièce. |
| `MAP_ROOM_BORDERS`, `MAP_BORDER_COLOR` | Trait de séparation entre deux pièces voisines et sa couleur. |
| `WEB_HOST`, `WEB_PORT` | Adresse et port de l'interface web. |

Si vous ne connaissez pas le token, laissez `VACUUM_TOKEN` vide : `python run.py --devices` affiche tous les appareils du compte avec leur token, leur identifiant et leur IP.

## Utilisation

```powershell
python run.py               # interface web sur http://127.0.0.1:5000 (le navigateur s'ouvre tout seul)
python run.py --setup       # rouvre la fenêtre de configuration
python run.py --devices     # liste les appareils du compte
python run.py --once        # enregistre la carte dans maps/map_latest.png et quitte
python run.py --debug       # journaux détaillés
python run.py --relogin     # force une nouvelle connexion
python run.py --no-local    # n'utilise que le cloud (pas de commande locale)
python run.py --no-gui      # tout dans le terminal (captcha enregistré dans captcha.png)
python run.py --no-browser  # n'ouvre pas le navigateur
```

Au premier lancement, Xiaomi peut demander un captcha ou un code de vérification envoyé par e-mail : ils sont demandés dans une fenêtre (ou dans le terminal avec `--no-gui`). La session est ensuite enregistrée dans `.xiaomi_session.json` pour ne pas se reconnecter à chaque fois.

## Points d'accès HTTP

| URL | Contenu |
| --- | --- |
| `/` | Interface web (carte, pièces, position du robot, bouton Actualiser). |
| `/map.png` | Dernière image de la carte. |
| `/api/status` | État du service (appareil, erreur éventuelle, dernière mise à jour, résumé de la carte). |
| `/api/map` | Données de la carte en JSON (pièces, murs, zones interdites, position du robot, points de calibration...). |
| `/api/refresh` (POST) | Force un rafraîchissement. |
| `/map.raw` | Données brutes telles que reçues du cloud. |

## Fonctionnement

1. Connexion au compte Xiaomi (même algorithme que *Xiaomi Cloud Tokens Extractor*).
2. Recherche du robot dans la liste des appareils (par token ou identifiant).
3. Demande du nom de la carte au robot : en local via le protocole miIO si l'IP et le token sont connus, sinon via le cloud (RPC).
4. Récupération de l'URL signée de la carte puis téléchargement.
5. Décodage avec les bibliothèques `vacuum-map-parser-*` (les mêmes que *Xiaomi Cloud Map Extractor* pour Home Assistant) et génération de l'image.

## Structure

```text
run.py                    point d'entrée
vacuum_map/gui.py         fenêtres de configuration, captcha et code de vérification (Tkinter)
vacuum_map/config.py      lecture du .env
vacuum_map/xiaomi_cloud.py  connexion et API cloud Xiaomi (login, 2FA, captcha, RPC, URLs de carte)
vacuum_map/miio_local.py  protocole local miIO (UDP + AES) avec le token
vacuum_map/vacuum.py      logique par constructeur : nom de carte, URL, décodage, image
vacuum_map/web.py         serveur Flask et rafraîchissement en arrière-plan
vacuum_map/templates/     page web
maps/                     cartes enregistrées (ignoré par git)
```

## Dépannage

- **Aucun appareil trouvé** : vérifiez `XIAOMI_SERVER` (compte européen = `de`). Laissez vide pour chercher partout.
- **Le cloud ne renvoie pas d'URL** : le robot n'a jamais envoyé de carte (lancez un nettoyage) ou le mauvais robot est sélectionné.
- **Commande locale échouée** : token ou IP incorrects, ou robot sur un autre réseau. Le programme bascule automatiquement sur le cloud ; `--no-local` désactive complètement l'accès local.
- **Décodage impossible** : lancez `python run.py --debug` et conservez `maps/map_latest.raw` pour analyse.
