# Noèse

**La mémoire vivante de la collectivité.** Un outil du Labo d’**État de l’Art**.

Noèse rassemble les connaissances d’une collectivité, quelle que soit leur origine : documents, pages web, savoirs oraux des agents, procédures et réponses types, et questions réellement posées par les habitants. Il les organise en une mémoire structurée, consultable, vérifiable et actualisable. La collectivité voit ce qu’elle sait, où se trouvent les manques, ce qui est à vérifier, et sur quelles sources tout cela repose.

L’IA aide à extraire, structurer et rapprocher l’information, mais elle ne valide rien : chaque connaissance est validée par un agent, et chaque réponse cite sa source.

> Logiciel libre sous licence EUPL 1.2.

## Les quatre espaces

| Espace | Adresse | Pour quoi faire |
|---|---|---|
| **Vue d’ensemble** | `/` | L’état de la mémoire : couverture des questions, lacunes, connaissances à vérifier, mémoire constituée ; la mémoire par service ; les sujets à surveiller ; l’activité récente. Filtres par service et par période. |
| **Explorer la mémoire** | `/memoire` | Rechercher et parcourir toutes les connaissances et toutes les sources, par service et par sujet. Chaque connaissance montre sa provenance, sa date de validation et son statut. |
| **Enrichir la mémoire** | `/enrichir` | Un seul parcours pour ajouter un document, une page web, un savoir oral ou une connaissance saisie : fournir, préciser, relire, enregistrer. |
| **Lacunes et qualité** | `/lacunes` | Les besoins des habitants sans réponse fiable, regroupés et priorisés, et les connaissances à vérifier. Chaque lacune mène à une action. |

Les paramètres (services et référents, règles de fraîcheur, référents de la base, export) et la déconnexion sont dans le menu de l’en-tête.

### Le modèle

- **La collectivité** : ses paramètres et ses services.
- **Les sources** (`document`) : documents, pages web, savoirs oraux, saisies d’agents, avec leur service, leur sujet, leur auteur et leur date d’ajout.
- **Les connaissances** (`fiche` et `fiche_version`) : des informations rédigées et validées par un agent, rattachées à un service et à un sujet, avec leur source d’origine, leur historique et leur date de révision.
- **Les lacunes** (`groupe`) : les besoins des habitants mal couverts, obtenus en regroupant les questions similaires (`question`).

Une source peut donner plusieurs connaissances ; une connaissance répond à un besoin ; une lacune est résolue quand une connaissance validée la couvre. Les **sujets** (« Encombrants », « Cantine scolaire »…) viennent des questions des habitants : chaque question est reformulée en « sujet : précision » lors de l’analyse. Un agent peut corriger le sujet d’une lacune, et les analyses suivantes conservent son choix.

## Ce qui fonctionne

**Vue d’ensemble**
- Quatre indicateurs calculés sur les données réelles, chacun cliquable :
  - couverture des questions (pourcentage, et nombre de questions couvertes sur le total évalué) ;
  - lacunes (besoins distincts sans réponse, et réponses incomplètes) ;
  - connaissances à vérifier ;
  - mémoire constituée.
- La mémoire par service : connaissances validées, couverture avec une jauge, lacunes et fiches à vérifier. Un clic passe à la vue du service. Le tableau n’est pas un classement.
- Les sujets à surveiller, et l’activité récente : nouvelles connaissances, vérifications, sources ajoutées, analyses, lacunes résolues.

**Explorer la mémoire**
- Recherche dans les titres et dans le contenu de toutes les connaissances et sources. C’est une recherche par mots, avec leurs variantes (pluriels, conjugaisons), grâce à la recherche plein texte française de PostgreSQL, et non une recherche par le sens.
- Navigation par service et par sujet ; filtre par statut : validée, à vérifier bientôt, révision dépassée, signalée, proposée, retirée.
- Fiche de connaissance :
  - contenu structuré : réponse, conditions et exceptions, pièces, démarche ;
  - provenance : la source, ou la réponse d’origine du service ;
  - validation, prochaine révision et historique des versions ;
  - points d’incertitude ;
  - actions : corriger, signaler (erreur, contradiction, information dépassée…), demander une vérification au service, changer de service ou de sujet.
- Fiche de source : le texte lu, les connaissances qui en sont issues, les besoins des habitants qui s’appuient dessus ; on peut la classer ou la retirer.

**Enrichir la mémoire**
- **Document** : PDF, Word (.docx), Markdown et texte, plusieurs fichiers à la fois, avec une zone de dépôt.
- **Page web** : la page est récupérée et montrée avant l’ajout. On peut aussi ajouter les pages liées du même site, lues en arrière-plan.
- **Savoir oral** : enregistrement depuis le navigateur ou import d’un fichier audio, transcrits par le modèle configuré (Voxtral par défaut), plus des notes. L’audio n’est pas conservé, seul le texte l’est.
- **Saisie** : une règle, une procédure, une réponse, ou une information qui manque ou qui a changé.
- Parcours commun : la source est enregistrée et aussitôt consultable, des connaissances sont proposées à partir de son contenu, et l’agent les valide, les corrige ou les écarte. Une proposition n’est jamais comptée comme une connaissance avant validation.
- Les erreurs sont dites clairement : format non pris en charge, PDF scanné sans texte, page inaccessible, transcription en échec. Si la proposition automatique échoue, la source reste enregistrée.
- Import des questions d’habitants (CSV de chatbot ou de callbot, mails) et données de démonstration : « Enrichir la mémoire », puis « Importer des questions d’habitants ».

**Lacunes et qualité**
- Résumé : besoins sans réponse, réponses incomplètes, en cours de traitement, résolus ces 30 jours, connaissances à actualiser.
- Questions sans réponse fiable : un besoin par ligne, avec le nombre de questions, une formulation réelle d’habitant, le service, le sujet, le type, la priorité et le statut. Filtres par service, sujet, type, priorité et statut, et recherche.
- Priorité par une règle simple et affichée : haute à partir de 8 questions, ou de 4 si la moitié a été passée à un agent ; moyenne à partir de 3 ; basse sinon. Un agent peut la relever pour tenir compte de l’importance métier ou du risque.
- Passer à l’action depuis une lacune :
  - rédiger la connaissance ;
  - ajouter une source ou un savoir, rattachés à la lacune ;
  - demander au service par mail, sans compte pour lui ;
  - **Vérifier et clore** : la lacune n’est résolue que si la mémoire couvre désormais le besoin. Valider une connaissance rattachée à la lacune la revérifie automatiquement.
- Connaissances à vérifier : signalées par les agents, à la révision dépassée, à vérifier bientôt.

**Fraîcheur, services et partage** (déjà en place, inchangé)
- Demandes aux services par lien magique, réponse écrite ou dictée, relance après 5 jours ouvrés puis signalement.
- « Toujours juste » avant la date de révision.
- Connexion des référents par lien magique.
- Export Markdown, JSON et CSV réimportable.

## Ce qui reste à développer

- **Recherche par le sens** : elle n’existe pas encore. La recherche actuelle se fait par mots, avec leurs variantes. Les embeddings d’une offre Mistral supérieure ou de l’API Albert la rendraient possible.
- **Plusieurs sujets par connaissance** : une connaissance n’a qu’un sujet. Les processus métier ne sont pas encore modélisés à part, et les sujets en tiennent lieu.
- **Détection automatique des contradictions** entre sources : non disponible. Les contradictions remontent par les signalements des agents.
- **Péremption des sources** : aucune règle n’est définie. Seules les connaissances ont une date de révision.
- **Jeu de questions de référence** : la couverture porte sur les questions réelles des habitants ; un jeu de référence reste à définir.
- **Assignation d’une lacune à une personne** : elle passe par la demande au service. Il n’y a pas de responsable nommé en dehors de cette demande.
- **Fichiers volumineux** : les connaissances sont proposées à partir des 12 000 premiers caractères d’une source ; le reste du texte est conservé et cherchable.
- **Formats** : les anciens fichiers Word (.doc), les tableurs et les PDF scannés ne sont pas lus. Le message l’indique, avec une solution.

## Critères d'acceptation

| Critère | État |
|---|---|
| Au moins 80 % des trous volontaires retrouvés, chaque « couvert » cite une source réelle | 10 trous sur 10 lors de trois évaluations successives, aucun « couvert » sans source (`scripts/evaluer.py`) |
| Du mail à la fiche validée en moins de 3 minutes sur mobile | 57 s et 54 s lors de deux parcours chronométrés (saisie au clavier, dont 6 à 8 s d'attente du modèle) |
| Aucune fiche dans la base sans validation humaine | Contrainte en base, plus des tests sur la proposition, la validation sans nom, le réimport et l'export |
| Export JSON et Markdown lisibles et réimportables | Tests d'aller-retour sur Markdown, JSON et CSV |
| Contrastes 4,5:1, navigation au clavier | `scripts/contrastes.py` (tous au-dessus de 4,5:1, étiquettes de statut comprises) ; parcours au clavier vérifiés sur la recherche de la mémoire et la page de réponse |
| `docker compose up` lance tout, README en français | Fichiers écrits, **mais pas encore testés** : Docker n'était pas disponible sur la machine de développement |

## Limites connues

- Les évolutions de schéma passent par de petites migrations au démarrage (`app/migrations.py`), à reprendre dans Alembic avant les pilotes.
- Avec un petit modèle (`ministral-8b`), le regroupement de questions proches est parfois trop fin (l'outil « Rassembler » y remédie), et la mise en forme ajoute parfois une phrase : la relecture affiche la réponse d'origine de l'agent pour la repérer.
- La dictée n'a été testée qu'avec un fichier audio, pas avec un vrai micro sur téléphone.

**Connexion du référent base**
- Les quatre espaces, les paramètres et l'export demandent une connexion. Le référent tape son adresse et reçoit un **lien valable 15 minutes, utilisable une seule fois**. Il n'y a pas de mot de passe.
- La session dure 30 jours sur le poste. Retirer un référent dans les Paramètres coupe sa session immédiatement.
- La page répond toujours la même chose, que l'adresse soit connue ou non. Elle envoie au plus 5 liens par heure et par référent, et ne redirige jamais vers un autre site après la connexion.
- Les services n'ont toujours pas de compte : leurs pages (`/r/…`, `/v/…`) restent accessibles par leur propre lien.
- Le premier référent est créé au démarrage depuis `NOESE_REFERENT_BASE_EMAIL`. Les suivants s'ajoutent dans les Paramètres.
- En développement, sans serveur de mail (`NOESE_SMTP_HOST=journal`), le lien s'affiche à l'écran, avec la mention « Mode développement ».

## Démarrer avec Docker

```bash
cp .env.example .env   # puis renseignez NOESE_LLM_API_KEY, NOESE_SECRET et NOESE_REFERENT_BASE_EMAIL
docker compose up
```

- Application : http://localhost:8000
- Boîte mail de test : http://localhost:8025

Dans « Enrichir la mémoire », puis « Importer des questions d’habitants », le bouton « Charger les données de démonstration » installe Ville-Exemple, une commune fictive : 32 pages de site et 309 questions d’habitants. Ces données sont signalées « Données de démonstration » dans la Vue d’ensemble.

## Démarrer sans Docker (développement)

Il faut [uv](https://docs.astral.sh/uv/). Sans `NOESE_DATABASE_URL`, un PostgreSQL embarqué avec pgvector (paquet `pgserver`) démarre tout seul dans `.pgdata/`.

```bash
uv sync
uv run uvicorn app.main:app --reload
```

Le worker tourne alors dans le même processus. Avec Docker, il tourne dans son propre conteneur (`python -m app.taches`).

## Principes

1. **L'IA prépare, l'agent valide.** Une contrainte de la base de données interdit d'enregistrer une version de fiche validée sans nom de validateur ni date.
2. **Toujours une source.** Un groupe n'est classé « couvert » ou « partiel » que s'il cite au moins un extrait réellement trouvé dans la base. Sinon, c'est un trou.
3. **Souverain.** Un seul format d'API (compatible OpenAI) pour changer de fournisseur : Mistral par défaut, l'API Albert de l'État ou un modèle auto-hébergé. Les scripts sont servis par l'application, sans appel à un CDN, et l'interface utilise la police système.
4. **Même identité que le site.** La charte reprend celle d'[etatdelart.ai](https://etatdelart.ai/) : police système, encre `#0B0B0C`, panneaux `#F5F5F7`, bleu-violet `#3D3BD4` pour l'action, étiquettes de statut vertes, ambre et rouges. Les valeurs sont posées en variables au début de `app/static/css/noese.css`.
5. **Léger.** FastAPI, gabarits rendus côté serveur, HTMX (50 Ko), une seule base PostgreSQL (qui sert aussi de file de tâches), aucun framework front.
6. **Accessible.** RGAA comme plancher : contrastes vérifiés, focus visible, navigation au clavier, libellés explicites.

## Fournisseur de modèles

| Variable | Rôle |
|---|---|
| `NOESE_LLM_BASE_URL` | URL de l'API compatible OpenAI |
| `NOESE_LLM_API_KEY` | Clé d'API |
| `NOESE_LLM_CHAT_MODEL` | Modèle de dialogue (reformulation, regroupement, jugement) |
| `NOESE_LLM_EMBED_MODEL` | Modèle d'embeddings, ou `local` pour des embeddings lexicaux calculés sur place |
| `NOESE_LLM_STT_MODEL` | Modèle de transcription vocale |

Changer de modèle d'embeddings déclenche une réindexation complète à la prochaine analyse.

La recherche dans la base est hybride : la similarité vectorielle (pgvector) est combinée avec la recherche plein texte en français de PostgreSQL. L'outil reste ainsi fiable avec des embeddings modestes.

## Vérifier la qualité

```bash
uv run pytest                                    # tests hors ligne, fournisseur factice
uv run python -m scripts.evaluer --reinitialiser # évaluation sur Ville-Exemple, avec le vrai modèle
```

L'évaluation compare les verdicts à `demo/verite_terrain.yaml` : au moins 80 % des trous volontaires doivent être retrouvés, et chaque verdict « couvert » doit citer une source réelle.

## Arborescence

```
app/            application : modèles, analyse, mémoire (memoire.py), enrichissement (enrichir.py), worker, pages
app/templates/  gabarits Jinja
app/static/     CSS, HTMX, script de dictée
demo/           Ville-Exemple : site, questions, mails, vérité terrain
scripts/        génération des données de démo, évaluation
tests/          tests automatisés
```

## Licence

Noèse est un logiciel libre publié par État de l’Art sous la [licence publique de l'Union européenne (EUPL) v. 1.2](LICENSE). Le texte officiel en français est dans le fichier `LICENSE` ; les versions de l'EUPL dans toutes les langues de l'Union ont la même valeur.

HTMX est distribué sous licence BSD zéro clause (0BSD). L'interface utilise la police système de l'appareil, comme le site d'État de l’Art : aucune police n'est téléchargée.
