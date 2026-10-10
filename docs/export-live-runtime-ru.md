# Выгрузка рабочего программного кода из Railway без токенов

Восстановленный сайт успешно работает из приватного Railway-тома `web-sqlite-data` (`/data`). Репозиторий `Andrey12133311/eis223-auditor2` **публичный**, поэтому до публикации кода необходимо проверить его на встроенные пароли/ключи.

## На Windows, в PowerShell

Установите Railway CLI по официальной документации https://docs.railway.com/cli и войдите в нужную учётную запись:

```powershell
railway login
railway link
```

В `railway link` выберите проект **eis223-auditor**, окружение **production**, сервис **web**. Скачайте **только два файла исходного кода** с подключенного тома:

```powershell
railway volume files --volume web-sqlite-data download /_eis223_runtime_recovery/snapshot-20261010T145653Z-01838c56/entry.py ./entry.py
railway volume files --volume web-sqlite-data download /_eis223_runtime_recovery/snapshot-20261010T145653Z-01838c56/all_documents.py ./all_documents.py
```

Если имя каталога отличается, посмотрите список:

```powershell
railway volume files --volume web-sqlite-data list /_eis223_runtime_recovery/
```

Документация Railway: https://docs.railway.com/cli/volume — операция `files download` скачивает файл и не изменяет том.

## Передача для анализа

Прикрепите `entry.py` и `all_documents.py` к чату. Не прикрепляйте `program-env.json`, `manifest.json`, содержимое базы SQLite, токены, файлы из `.env` и дампы переменных Railway. Переменные из бэкапа содержат рабочую конфигурацию и не предназначены для публичного репозитория.

После получения только кода нужно: (1) проверить исходники на встроенные секреты, (2) сравнить с GitHub `app.py`, (3) перенести работающие модули в исходники, (4) протестировать юридические проверки и загрузку оригиналов, (5) отдельно внедрить ограниченную повторную загрузку недоступных файлов, (6) только затем запускать обновление.

**Не удаляйте папку `/_eis223_runtime_recovery`: текущий запущенный сервис продолжает зависеть от неё.**
