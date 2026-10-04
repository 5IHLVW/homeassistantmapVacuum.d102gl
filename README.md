# Home Assistant Map Vacuum d102gl

Intégration Home Assistant qui affiche la carte d'un aspirateur robot Xiaomi (testée avec le modèle `xiaomi.vacuum.d102gl`) en la téléchargeant depuis le cloud Xiaomi. Elle fonctionne aussi avec les autres robots vendus par Xiaomi : Roborock / Mi Robot, Dreame, Viomi, Roidmi, Ijai et les modèles `xiaomi.vacuum.*`.

Entités créées :

- `camera.<robot>_carte` : image de la carte, avec les attributs `calibration_points`, `rooms`, `vacuum_position`, `charger`, `walls`, `no_go_areas`... attendus par la carte Lovelace [Xiaomi Vacuum Map Card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card).
- `image.<robot>_carte` : la même carte sous forme d'entité image.
- `button.<robot>_actualiser_la_carte` : force une mise à jour.

## Installation via HACS

1. Dans HACS, ouvrez le menu (trois points en haut à droite) puis **Dépôts personnalisés**.
2. Ajoutez `https://github.com/5IHLVW/homeassistantmapVacuum.d102gl` avec la catégorie **Intégration**.
3. Installez **Home Assistant Map Vacuum d102gl** puis redémarrez Home Assistant.

Installation manuelle : copiez le dossier `custom_components/map_vacuum_d102gl` dans le dossier `custom_components` de votre configuration Home Assistant, puis redémarrez.

## Configuration

**Paramètres > Appareils et services > Ajouter une intégration > Map Vacuum d102gl**.

1. Identifiant et mot de passe du compte Mi Home, serveur (`Europe (de)` pour un compte européen, ou `Automatique`).
2. Si Xiaomi le demande : captcha (affiché dans le formulaire) puis code de vérification reçu par e-mail.
3. Choix du robot dans la liste du compte. Le token et l'adresse IP sont récupérés automatiquement depuis le cloud.

Options (icône **Configurer** sur l'intégration) : intervalle de rafraîchissement, échelle et rotation de l'image, couleurs de la carte, envoi des commandes en local, API du constructeur.

Couleurs : thème **Clair** (fond blanc, pièces pastel, par défaut), **Contraste** (fond blanc, couleurs franches) ou **Original** (fond bleu). Vous pouvez aussi choisir la couleur du fond, tracer ou non un trait entre les pièces, et imposer vos propres couleurs de pièces (`#F4A9A3 #A7C8F2 ...`, dans l'ordre des numéros de pièce).

La session cloud est conservée dans `.storage/`, il n'est donc pas nécessaire de refaire le captcha à chaque redémarrage. Si elle expire, Home Assistant propose une nouvelle authentification.

## Exemple de carte Lovelace

Avec [Xiaomi Vacuum Map Card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card) (installable via HACS) :

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.oscar_wipe          # votre entité vacuum (Xiaomi Miio ou autre)
map_source:
  camera: camera.oscar_wipe_carte
calibration_source:
  camera: true
vacuum_platform: default
```

Sans entité `vacuum`, une simple carte image suffit :

```yaml
type: picture-entity
entity: camera.oscar_wipe_carte
camera_view: live
```

## Fonctionnement

1. Connexion au compte Xiaomi (algorithme de *Xiaomi Cloud Tokens Extractor*).
2. Demande du nom de la carte au robot, en local (protocole miIO avec le token) ou via le cloud.
3. Récupération de l'URL signée de la carte et téléchargement.
4. Décodage avec les bibliothèques `vacuum-map-parser-*` (celles de *Xiaomi Cloud Map Extractor*).

Le code du cœur (`custom_components/map_vacuum_d102gl/core/`) est partagé avec l'application autonome du dossier [`standalone`](standalone/), utilisable sans Home Assistant.

## Publication sur le dépôt HACS par défaut

Pour que l'intégration apparaisse directement dans HACS sans ajout de dépôt personnalisé :

1. Le dépôt GitHub doit être public, avec une **description** et au moins un **topic** (par exemple `home-assistant`, `hacs`).
2. Publier une **release** GitHub (par exemple `v1.0.0`).
3. Ajouter l'intégration à [home-assistant/brands](https://github.com/home-assistant/brands) (icône et logo dans `custom_integrations/map_vacuum_d102gl/`).
4. Ouvrir une pull request sur [hacs/default](https://github.com/hacs/default) en ajoutant le dépôt au fichier `integration`.

Les actions GitHub `hacs/action` et `hassfest` du dépôt vérifient automatiquement la conformité à chaque push.

## Dépannage

- **Aucun aspirateur trouvé** : vérifiez le serveur choisi (compte européen = `de`).
- **Le cloud ne renvoie pas d'URL** : le robot n'a jamais envoyé de carte, lancez un nettoyage.
- **Journaux détaillés** : ajoutez dans `configuration.yaml`

```yaml
logger:
  logs:
    custom_components.map_vacuum_d102gl: debug
```
