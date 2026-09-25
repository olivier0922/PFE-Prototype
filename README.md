# Météo sur le réseau — prototype PFE Hydro-Québec

Application Dash qui projette les analyses ERA5 de l'événement de verglas des
29–30 décembre 1942 sur les corridors de transport d'Hydro-Québec, sous forme
de **coupes distance–altitude** lisibles en contexte opérationnel.

## Ce que fait l'outil

| Vue | Question à laquelle elle répond |
| --- | --- |
| Carte du réseau | Quels corridors sont touchés à cette heure? Cliquer un corridor le sélectionne. |
| Coupe atmosphérique | Quelle est la structure verticale au-dessus de la ligne (isothermes, couche de fonte, vent, relief)? |
| Bande « Précip. » | Quel type de précipitation la structure thermique favorise-t-elle (neige, grésil, verglas, pluie)? |
| Indice le long du corridor | Quels kilomètres sont les plus exposés, et à quel aléa? |
| Évolution sur 48 h | Où et quand l'indice culmine-t-il? Un clic choisit l'heure et le point. |
| Sondage vertical | Profil T / point de rosée / vent à un point cliqué. |
| Tronçons exposés | Tableau prêt pour la décision; un clic isole le tronçon dans toutes les vues. |

### Coupe interactive

- **Fond**: isolignes remplies de la variable choisie (isothermes aux 2 °C pour
  la température).
- **Calques** superposables: isotherme 0 °C, couche de fonte (air > 0 °C
  au-dessus d'air < 0 °C près des conducteurs), vecteurs de vent, niveaux ERA5,
  et les isolignes de n'importe quelle autre variable (vent, θ, HR…).
- **Vecteurs de vent**: vent horizontal vu du dessus (nord en haut), longueur
  proportionnelle à la vitesse; l'infobulle donne les composantes
  perpendiculaire et parallèle à la ligne.
- **Zoom / translation**: glisser pour zoomer, molette, barre d'outils Plotly
  pour la translation; double-clic pour revoir tout le corridor. Le zoom
  horizontal est borné au corridor, le zoom vertical au plafond choisi.
- **Tronçon**: le curseur sous la coupe, le zoom de la coupe et le tableau des
  tronçons exposés partagent la même sélection. Elle filtre les indicateurs,
  cadre le diagramme 48 h et recentre la carte.
- **Plein écran**: le bouton ⤢ de chaque panneau (Échap pour revenir); les
  contrôles de la coupe restent accessibles en plein écran.

### Ajouter une variable

Toutes les variables de la coupe sont décrites dans `src/variables.py`. Une
nouvelle variable est une seule entrée `Variable(...)` dans `REGISTRY`: clé,
libellé, unité, fonction de calcul sur la grille, palette, bornes et pas des
isolignes. Elle apparaît automatiquement comme fond, comme isolignes et dans
les infobulles. Si elle dépend d'un champ ERA5 optionnel (`requires={"vo"}`),
elle n'est proposée que lorsque ce champ est présent dans le fichier.
Le site statique reprend la même liste dans `docs/app.js` (`VARIABLES` et
`field` calculés dans `grids`) : une variable ajoutée ici doit y être recopiée.

## Site statique (GitHub Pages)

Le dépôt publie la même vue sur GitHub Pages. Le serveur Dash ne peut pas
tourner là: à chaque poussée sur `main`, le workflow
`.github/workflows/pages.yml` calcule les corridors et dépose un site statique
(`docs/`) à l'adresse <https://olivier0922.github.io/PFE-Prototype/>.

Dans les réglages du dépôt, **Pages** doit utiliser la source **GitHub Actions**.

Export local, par exemple pour vérifier le site avant de le pousser:

```powershell
.\.venv\Scripts\python.exe scripts\export_pages.py
python -m http.server 8080 --directory docs
```

Puis ouvrir <http://127.0.0.1:8080>.

## Démarrage sous Windows

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\lancer_app.ps1
```

Puis ouvrir <http://127.0.0.1:8050>. Le premier démarrage crée `.venv`,
installe les dépendances, extrait le Zarr et classe l'ensemble du réseau
(environ 10 s).

Étapes séparées:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\prepare_data.py
.\.venv\Scripts\python.exe app.py
```

Le fond de carte OpenStreetMap/CARTO nécessite un accès Internet. L'option
« Schéma (hors ligne) » dessine le réseau sur des axes simples sans aucun
serveur externe.

## Données

- **ERA5 niveaux de pression** (`era5-pressure-levels-19421229-30.zarr.zip`):
  48 heures, grille 0,25°, niveaux 1000/850/700/500/250 hPa; variables
  utilisées: température, humidité relative, vent U/V, géopotentiel.
- **Lignes de transport** (`lignes_transport.geojson`): 3 867 segments,
  1 911 numéros de ligne, WGS84.
- **Relief ETOPO** (`mnt_ETOPO.tiff`): 15″ d'arc, WGS84.

## Méthode

### 1. Des segments GIS aux corridors

Les opérateurs raisonnent en numéros de ligne, pas en segments. Les segments
aériens d'un même `LIGNE` sont fusionnés topologiquement, puis les morceaux
séparés par un écart inférieur à 1,5 km sont chaînés. Les corridors de moins
de 5 km (raccordements de poste) sont exclus: ils sont sans signification face
à une grille de 25 km. Résultat: 580 corridors dans le domaine ERA5.

### 2. Projection sur la ligne

Chaque corridor est échantillonné tous les 2,5 km (distance géodésique
WGS84). Les champs ERA5 sont interpolés bilinéairement à chaque point, pour
les 48 heures d'un coup.

### 3. Interpolation verticale

Les cinq niveaux sont **interpolés linéairement en pression**. Le
géopotentiel donne l'altitude de chaque niveau; comme cette altitude est
elle-même linéaire en pression entre deux niveaux consécutifs, l'interpolation
linéaire en altitude sur le même intervalle produit exactement les mêmes
valeurs. La coupe est donc tracée sur une grille d'altitude de 100 m tout en
respectant la consigne.

Sous le niveau 1000 hPa (qui flotte 100–300 m au-dessus de la mer), la
température est extrapolée avec le gradient standard (6,5 °C/km); humidité et
vent sont maintenus constants. L'atmosphère sous le relief est masquée. Le
calque « Niveaux ERA5 » de la coupe rappelle où se trouvent les niveaux réels.

### 4. Diagnostic du type de précipitation

Méthode simplifiée par couches, quand la basse troposphère est proche de la
saturation (HR moyenne 0–1,5 km ≥ 75 %):

- T ≈ 100 m au-dessus du sol > 1 °C → pluie; 0–1 °C → neige mouillée;
- T ≤ 0 °C avec couche chaude (max 0,3–3 km) ≥ 1 °C et T ≥ −8 °C → verglas;
- couche chaude plus faible ou surface plus froide → grésil;
- pas de couche chaude → neige.

### 5. Indicateur synthétique

Quatre aléas, chacun entre 0 et 1 (`src/risk.py`):

- **Pluie verglaçante**: surface froide × couche chaude × humidité;
- **Givrage / neige collante**: −7 à +2 °C et air quasi saturé (plafonné à 0,6);
- **Vent fort**: 40 → 90 km/h à ≈100 m au-dessus du sol;
- **Froid extrême**: −20 → −35 °C.

L'indice 0–100 suit l'aléa dominant, majoré de 15 % de la somme des autres.
Catégories: Faible < 25, Modéré 25–49, Élevé 50–74, Critique ≥ 75.

## Limites

- Les données ne contiennent ni précipitations, ni eau liquide, ni
  hydrométéores: l'indice est un **potentiel atmosphérique**, pas une
  prévision validée d'accumulation.
- Cinq niveaux de pression ne résolvent pas la couche limite; le vent à 100 m
  et les couches chaudes minces sont approximatifs.
- ERA5 à 25 km lisse les effets locaux du relief.
- L'événement de 1942 est un cas de démonstration. En exploitation, les mêmes
  modules acceptent n'importe quel jeu `(time, level, latitude, longitude)`
  avec `t`, `r`, `u`, `v`, `z`.
- Les seuils doivent être calibrés avec les observations et l'historique
  d'interruptions avant tout usage décisionnel.

## Structure

- `app.py`: interface et callbacks Dash;
- `src/network.py`: fusion des segments en corridors, échantillonnage;
- `src/data.py`: extraction, validation, accès ERA5 / relief / corridors;
- `src/interpolation.py`: interpolation spatiale et verticale vectorisée;
- `src/variables.py`: registre des variables de la coupe;
- `src/risk.py`: aléas, type de précipitation, segments exposés;
- `src/overview.py`: classement de tous les corridors;
- `src/figures.py`: figures Plotly et calques de la coupe;
- `assets/`: styles et script du plein écran;
- `tests/`: 17 tests unitaires et d'intégration (`.\.venv\Scripts\python.exe -m pytest -q`).
