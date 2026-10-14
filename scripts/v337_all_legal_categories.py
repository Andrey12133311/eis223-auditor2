"""V337 expanded 223-FZ coverage catalog.

Categories are REVIEW SIGNALS ONLY. A regex match never proves a violation.
Confirmed infringements remain the responsibility of narrow source-verified
validators (date chains, legal NMCD/bid security, applicable SME criteria).
Entries needing an EIS event, legal exception, or procurement regulation
remain unassessed until that source can be checked.
"""
from __future__ import annotations
import re
from functools import wraps
from fastapi import HTTPException

from v331_broad_legal import verified
from v331_broad_legal import CATALOG as OLD_CATALOG
from v336_legal_matrix import CATEGORIES as V336_CATEGORIES

BASIS = {
  "regulation": "223-ФЗ, ст. 2 и ст. 3",
  "planning": "223-ФЗ, ст. 4 ч. 2–3; ПП РФ № 932",
  "notices": "223-ФЗ, ст. 4 ч. 9",
  "docs": "223-ФЗ, ст. 4 ч. 6.1, 8, 10",
  "dates": "223-ФЗ, ст. 3.2 и ст. 4 ч. 11–12",
  "access": "223-ФЗ, ст. 3 ч. 1 и ч. 6",
  "application": "223-ФЗ, ст. 3, 3.2, 3.4",
  "security": "223-ФЗ, ст. 3.2 ч. 25–27; ст. 3.4",
  "sme": "223-ФЗ, ст. 3.4; ПП РФ № 1352",
  "procedure": "223-ФЗ, ст. 3.2–3.5",
  "evaluation": "223-ФЗ, ст. 3, 3.2; ст. 4 ч. 10",
  "protocols": "223-ФЗ, ст. 3.2; ст. 4 ч. 12",
  "contract": "223-ФЗ, ст. 3 ч. 5.3; ст. 3.2; ст. 4.1",
  "national": "223-ФЗ, ст. 3.1-4; ПП РФ № 1875",
  "reporting": "223-ФЗ, ст. 4, 4.1, 5; ПП РФ № 908"
}

RAW = r"""regulation~regulation_absent~Нет применимого положения о закупке~положени~положени\w*\s+о\s+закупк\w*.{0,120}?(?:не\s+утвержден\w*|отсутству\w*|не\s+размещен\w*)
regulation~regulation_old~Ссылка на неактуальную редакцию положения~редакци~(?:действу\w*\s+редакци\w*|редакци\w*\s+положени\w*).{0,110}?(?:утратил\w*\s+силу|не\s+действу\w*)
regulation~method_regulation_mismatch~Способ отсутствует в положении о закупке~положени~(?:способ\s+закупк\w*|вид\s+закупк\w*).{0,100}?не\s+предусмотрен\w*\s+положени\w*
regulation~extra_qualification~Неизмеримые требования к квалификации~оценк~(?:квалификаци\w*|профессионализм\w*).{0,110}?(?:по\s+усмотрени\w*|субъективн\w*|без\s+критери\w*)
regulation~exceptions_nocontract~Исключения для закупки у единственного поставщика~единственн~(?:единственн\w*\s+поставщик\w*|неконкурентн\w*\s+закупк\w*).{0,140}?(?:без\s+основани\w*|не\s+предусмотрен\w*\s+положени\w*)
planning~plan_outside~Закупка не включена в действующий план~план~(?:закупк\w*|позици\w*).{0,105}?(?:не\s+включен\w*\s+в\s+план|отсутству\w*\s+в\s+плане)
planning~plan_mismatch~Несоответствие плана и извещения~план~(?:план\w*\s+закупк\w*|планируем\w*\s+закупк\w*).{0,140}?(?:расходят\w*|не\s+соответств\w*)
planning~plan_innovation~Долгосрочное планирование инновационной продукции~инновацион~(?:инновационн\w*|высокотехнологичн\w*).{0,100}?(?:план\w*|период\w*\s+планировани\w*)
planning~plan_medicines~Планирование закупок лекарственных средств~лекарствен~(?:план\w*\s+закупк\w*|закупк\w*).{0,90}?лекарственн\w*\s+средств\w*
planning~plan_change~Изменение плана после публикации извещения~план~(?:изменени\w*\s+план\w*|корректировк\w*\s+план\w*).{0,100}?после\s+размещени\w*\s+извещени\w*
planning~plan_year~Планирование менее установленного периода~план~(?:план\w*\s+закупк\w*).{0,90}?(?:на\s+\d+\s+месяц\w*|на\s+квартал)
notices~notice_missing_data~Не заполнены обязательные сведения извещения~извещени~извещени\w*.{0,120}?(?:не\s+указан\w*|отсутству\w*).{0,80}?(?:срок|цен\w*|заказчик\w*|предмет)
notices~notice_customer_contacts~Сведения о заказчике и контактах в извещении~контакт~(?:извещени\w*|карточк\w*\s+закупк\w*).{0,130}?контактн\w*\s+информаци\w*
notices~notice_procurement_subject~Недостаточная определённость предмета закупки~предмет~предмет\s+закупк\w*.{0,115}?(?:определ\w*\s+позднее|по\s+усмотрени\w*\s+заказчик\w*|уточн\w*\s+после)
notices~notice_method~Расхождение способа закупки~способ~способ\s+закупк\w*.{0,110}?(?:не\s+соответств\w*|указан\w*\s+ошибочн\w*)
notices~notice_financing~Сведения о цене или порядке её определения~цен~(?:цен\w*\s+договора|порядок\s+определени\w*\s+цен\w*).{0,110}?(?:будет\s+определ\w*|по\s+усмотрени\w*)
notices~notice_sme_label~Правовой статус закупки среди МСП~мсп~(?:закупк\w*\s+среди\s+мсп|для\s+субъект\w*\s+мсп|только\s+субъект\w*\s+мсп)
notices~notice_multi_lot~Отдельные НМЦД и сроки по лотам~лот~(?:по\s+лот\w*|лот\s+№).{0,130}?(?:цен\w*|обеспечени\w*|срок\w*)
notices~notice_currency~Валюта и формула цены~валют~(?:цен\w*\s+договора|нмцд).{0,110}?(?:иностранн\w*\s+валют\w*|курс\w*\s+валют\w*)
notices~notice_advance~Аванс и порядок расчётов~аванс~(?:аванс|предварительн\w*\s+оплат\w*).{0,110}?(?:по\s+усмотрени\w*|не\s+определ\w*)
docs~documentation_notice_contradiction~Противоречие извещения и документации~извещени~(?:документаци\w*|проект\w*\s+договор\w*).{0,150}?(?:расхождени\w*\s+с\s+извещени\w*|противореч\w*\s+извещени\w*)
docs~docs_inaccessible~Недоступный или нечитаемый файл документации~документ~(?:файл\w*\s+документаци\w*|приложени\w*\s+к\s+документаци\w*).{0,100}?(?:поврежден\w*|не\s+открыва\w*|не\s+доступ\w*)
docs~documents_late~Опубликование документации после извещения~документаци~документаци\w*.{0,125}?(?:размещен\w*\s+позднее|опубликован\w*\s+после)\s+извещени\w*
docs~specification_open_ended~Неизмеримые требования к товару~соответств~(?:соответств\w*|показател\w*|характеристик\w*).{0,95}?(?:по\s+усмотрени\w*\s+заказчик\w*|определ\w*\s+после\s+поставки)
docs~brand_equivalence~Использование товарного знака без эквивалента~эквивалент~(?:товарн\w*\s+знак\w*|торгов\w*\s+марк\w*|бренд\w*).{0,130}?(?:без\s+возможност\w*\s+эквивалент\w*|эквивалент\s+не\s+допускается)
docs~lot_combining~Объединение функционально несвязанных товаров~лот~(?:лот\w*|один\s+лот).{0,115}?(?:функционально\s+не\s+связанн\w*|технологически\s+не\s+связанн\w*)
docs~manufacturer_certificates~Необоснованные письма изготовителя в заявке~производител~(?:участник\w*|заявк\w*).{0,125}?(?:письм\w*\s+производител\w*|авторизационн\w*\s+письм\w*)
docs~unpublished_tech_appendix~Отсутствие технического задания в доступных материалах~техническ~(?:техническ\w*\s+задани\w*).{0,95}?(?:не\s+приложен\w*|отсутству\w*|не\s+размещен\w*)
docs~technical_ambiguity~Технические параметры по результатам переговоров~характеристик~(?:характеристик\w*|комплектаци\w*).{0,90}?уточн\w*\s+(?:при\s+заключени\w*|после\s+побед\w*)
docs~project_contract_missing~Отсутствие проекта договора~проект~проект\s+договор\w*.{0,75}?(?:отсутству\w*|не\s+приложен\w*|не\s+размещен\w*)
docs~multiple_versions~Несовпадение редакций технической документации~редакци~(?:предыдущ\w*\s+редакци\w*|измененн\w*\s+редакци\w*).{0,100}?(?:техническ\w*\s+задани\w*|документаци\w*)
dates~submission_start~Начало и окончание приёма заявок~подачи заявок~(?:начал\w*|окончани\w*)\s+срока\s+подачи\s+заявок
dates~short_bid_period~Сокращение срока подачи заявок~срок~срок\s+подач\w*\s+заявк\w*.{0,100}?(?:сокраща\w*|уменьша\w*|продлен\w*\s+не\s+будет)
dates~late_changes~Изменения после предельного срока~изменени~(?:изменени\w*\s+извещени\w*|изменени\w*\s+документаци\w*).{0,130}?(?:после\s+окончани\w*\s+подач\w*|позднее\s+установленн\w*\s+срока)
dates~change_extension~Продление срока после изменений~продлен~(?:изменени\w*\s+документаци\w*|изменени\w*\s+извещени\w*).{0,150}?продлен\w*\s+срок\w*
dates~publication_lag~Промежуток между решением об изменении и размещением~размещени~(?:решени\w*\s+об\s+изменени\w*|изменени\w*\s+документаци\w*).{0,150}?размещени\w*\s+в\s+еис
dates~clarification_lastday~Последняя дата направления запроса разъяснений~запрос~(?:запрос\w*\s+разъяснени\w*|подач\w*\s+запрос\w*).{0,115}?(?:не\s+позднее|до\s+\d{1,2}[.]\d{1,2}[.]20\d{2})
dates~clarification_late_answer~Срок предоставления разъяснений~разъяснени~(?:разъяснени\w*\s+документаци\w*|ответ\w*\s+на\s+запрос\w*).{0,120}?(?:рабоч\w*\s+дн\w*|размещен\w*\s+в\s+еис)
dates~clarification_change_subject~Разъяснение изменяет предмет закупки~разъяснени~разъяснени\w*.{0,140}?(?:изменя\w*\s+предмет\s+закупк\w*|изменя\w*\s+существенн\w*\s+услови\w*)
dates~cancel_procedure~Отмена конкурентной закупки~отмен~(?:отмен\w*\s+закупк\w*|решени\w*\s+об\s+отмене).{0,130}?(?:подач\w*\s+заявк\w*|протокол\w*)
dates~new_deadline_notification~Информирование о новом сроке подачи заявок~нов\w*\s+срок~(?:нов\w*\s+срок\w*\s+подачи\s+заявок|срок\s+подачи\s+заявок\s+перенос\w*)
access~eligible_participants~Дискриминационное ограничение допуска~допускают~(?:к\s+участию\s+допускают\w*|заявк\w*\s+принимают\w*).{0,100}?(?:исключительно|только)\s+(?:местн\w*|региональн\w*|крупн\w*|производител\w*)
access~license_necessity~Требование лицензии без проверки необходимости~лицензи~(?:участник\w*|поставщик\w*).{0,90}?(?:обязан\w*|должен).{0,100}?лицензи\w*
access~registration_city~Местная регистрация участника~регистрац~(?:участник\w*|поставщик\w*).{0,75}?(?:зарегистрирован\w*|иметь\s+регистраци\w*).{0,70}?в\s+(?:регионе|городе|субъекте)
access~nationality_restriction~Ограничение допуска по резидентству~резидент~(?:к\s+участию|участник\w*).{0,90}?(?:только|исключительно).{0,60}?резидент\w*
access~experience_affiliation~Опыт договоров с конкретным заказчиком~опыт~(?:опыт\w*|выполненн\w*\s+договор\w*).{0,120}?(?:с\s+данным\s+заказчик\w*|с\s+нашей\s+организаци\w*)
access~own_equipment~Требование собственного оборудования~собственн~(?:участник|исполнитель).{0,120}?собственн\w*\s+(?:оборудовани\w*|техник\w*|транспорт\w*)
access~own_staff~Запрет привлекать работников вне штата~штат~(?:работник\w*|специалист\w*).{0,100}?(?:только\s+в\s+штате|исключительно\s+в\s+штате)
access~sro_membership~Обязательное членство СРО~сро~(?:участник\w*|исполнитель).{0,140}?(?:членств\w*\s+в\s+сро|свидетельств\w*\s+сро)
access~office_proximity~Наличие офиса в регионе заказчика~офис~(?:офис\w*|представительств\w*).{0,95}?(?:в\s+городе|в\s+регионе|на\s+территории)
access~vendor_register~Обязательная регистрация в реестре поставщиков заказчика~реестр~(?:участник\w*|поставщик\w*).{0,120}?(?:реестр\w*\s+поставщик\w*|аккредитаци\w*\s+у\s+заказчика)
access~on_site_visit~Обязательный предварительный осмотр объекта~осмотр~(?:участник\w*|поставщик\w*).{0,95}?(?:обязан\w*|должен).{0,90}?(?:осмотр\s+объект\w*|посетить\s+объект)
access~extra_fee~Плата за участие или подачу заявки~плат~(?:за\s+подачу\s+заявк\w*|за\s+участи\w*\s+в\s+закупк\w*).{0,100}?(?:плата|взнос|сбор)
application~application_contents~Требования к составу заявки~состав~(?:состав\s+заявк\w*|содержани\w*\s+заявк\w*).{0,130}?(?:установл\w*|обязательн\w*|содерж\w*)
application~application_document_list~Документы сверх установленного перечня~дополнительн~(?:дополнительн\w*\s+документ\w*|документ\w*\s+сверх\s+перечн\w*).{0,130}?(?:заявк\w*|участник\w*)
application~notarized_papers~Нотариально заверенные копии в составе заявки~нотариал~(?:в\s+составе\s+заявк\w*|к\s+заявк\w*).{0,135}?нотариальн\w*\s+заверен\w*
application~commercial_offer~Индивидуальные коммерческие предложения в заявке~коммерческ~(?:заявк\w*|участник\w*).{0,110}?(?:коммерческ\w*\s+предложени\w*|прайс[- ]лист)
application~dealer_letter~Письмо производителя в заявке~письм~(?:заявк\w*|участник\w*).{0,130}?(?:письм\w*\s+производител\w*|авторизационн\w*\s+письм\w*)
application~bank_certificates~Банковские справки в составе заявки~банк~(?:в\s+составе\s+заявк\w*|участник\w*).{0,105}?(?:справк\w*\s+банк\w*|банк\w*\s+справк\w*)
application~paper_originals~Бумажные оригиналы одновременно с электронной заявкой~бумажн~(?:электронн\w*\s+заявк\w*|в\s+электронн\w*\s+форме).{0,145}?(?:бумажн\w*\s+оригинал\w*|на\s+бумажн\w*\s+носител\w*)
application~application_withdrawal~Порядок отзыва заявки~отзыв~(?:отзыв\w*\s+заявк\w*|заявк\w*\s+отозва\w*).{0,95}?(?:до\s+окончани\w*|после\s+окончани\w*)
application~application_change~Изменение заявки до окончания срока~изменени~(?:изменени\w*\s+заявк\w*|внести\s+изменени\w*\s+в\s+заявк\w*).{0,80}?(?:до\s+окончани\w*|после\s+окончани\w*)
application~only_original_sign~Обязательная подпись дополнительным способом~подпис~(?:заявк\w*|документ\w*).{0,120}?(?:оригинал\w*\s+подпис\w*|подпис\w*\s+на\s+кажд\w*\s+страниц\w*)
security~bid_security_clause~Установление обеспечения заявки~обеспечени~обеспечени\w*\s+заявк\w*.{0,115}?(?:устанавлива\w*|составля\w*|размер\w*|\d+\s*%)
security~contract_security_clause~Обеспечение исполнения договора~обеспечени~обеспечени\w*\s+(?:исполнени\w*\s+)?договор\w*.{0,115}?(?:составл\w*|устанавлива\w*|размер\w*|\d+\s*%)
security~security_cash_only~Исключительно денежная форма обеспечения~денежн~(?:обеспечени\w*\s+заявк\w*|обеспечени\w*\s+договора).{0,145}?(?:только\s+денежн\w*|исключительно\s+денежн\w*)
security~security_no_return~Невозврат обеспечения заявки~возвращ~обеспечени\w*\s+заявк\w*.{0,145}?(?:не\s+возвраща\w*|удержива\w*\s+заказчик\w*)
security~security_guarantee_requirements~Требования к независимой гарантии~гаранти~(?:банковск\w*|независим\w*)\s+гаранти\w*.{0,120}?(?:обязательн\w*\s+услови\w*|срок\w*\s+действи\w*)
security~security_disproportion~Соразмерность обеспечения заявки НМЦД~обеспечени~(?:обеспечени\w*\s+заявк\w*).{0,110}?(?:процент\w*|от\s+нмцд|\d+\s*%)
security~security_advance~Обеспечение возврата аванса~аванс~(?:обеспечени\w*|гаранти\w*).{0,115}?возврат\w*\s+аванс\w*
sme~sme_only_restriction~Подтверждение закупки только среди МСП~мсп~(?:только|исключительно)\s+(?:для\s+)?(?:субъект\w*\s+)?мсп
sme~sme_electronic_form~Электронная форма закупки среди МСП~мсп~(?:электронн\w*\s+форм\w*|электронн\w*\s+площадк\w*).{0,110}?мсп
sme~sme_quote_limit~Предел НМЦД запроса котировок для МСП~котировок~запрос\s+котировок.{0,120}?(?:мсп|субъект\w*\s+мал\w*\s+предпринимател\w*)
sme~sme_proposal_limit~Предел НМЦД запроса предложений для МСП~предложени~запрос\s+предложени\w*.{0,120}?(?:мсп|субъект\w*\s+мал\w*\s+предпринимател\w*)
sme~sme_stage_rules~Этапы конкурса для МСП~этап~(?:этап\w*\s+конкурс\w*|конкурс\w*\s+в\s+нескольк\w*\s+этап\w*).{0,90}?мсп
sme~sme_collective~Коллективный участник в закупке среди МСП~коллективн~(?:коллективн\w*\s+участник\w*|групп\w*\s+лиц).{0,160}?(?:мсп|субъект\w*\s+мал\w*)
sme~sme_registry~Проверка нахождения в реестре МСП~реестр~(?:реестр\w*\s+мсп|единый\s+реестр\w*\s+субъект\w*\s+мсп)
sme~sme_declaration~Недопустимые дополнительные декларации МСП~деклараци~(?:участник\w*|заявк\w*).{0,135}?деклараци\w*\s+(?:мсп|субъект\w*\s+мал\w*)
sme~sme_payment~Срок оплаты договора с МСП~оплат~(?:мсп|мал\w*\s+предпринимател\w*).{0,150}?(?:срок\w*\s+оплат\w*|рабоч\w*\s+дн\w*)
sme~sme_subcontract~Субподряд МСП и плановые показатели~субподряд~(?:субподрядчик\w*\s+из\s+числа\s+мсп|привлечени\w*\s+субъект\w*\s+мсп)
sme~sme_quota~Годовой объём закупок у МСП~годов~(?:годов\w*\s+объем\w*|дол\w*\s+закупк\w*).{0,115}?мсп
sme~sme_special_account~Специальный счёт для обеспечения заявки МСП~специальн~(?:специальн\w*\s+счет\w*|спецсчет\w*).{0,125}?(?:мсп|обеспечени\w*\s+заявк\w*)
sme~sme_bid_security~Особый порядок обеспечения заявок МСП~мсп~(?:мсп|субъект\w*\s+мал\w*).{0,135}?обеспечени\w*\s+заявк\w*
sme~sme_penalties~Неустойка и штрафы по договору МСП~неустойк~(?:мсп|мал\w*\s+предпринимател\w*).{0,140}?(?:неустойк\w*|штраф\w*|пен\w*)
procedure~electronic_platform~Использование электронной торговой площадки~электронн~(?:электронн\w*\s+торгов\w*\s+площадк\w*|этп).{0,115}?(?:заявк\w*|торг\w*)
procedure~operator_fee~Тарифы и вознаграждение оператора ЭТП~оператор~(?:оператор\w*\s+электронн\w*\s+площадк\w*).{0,110}?(?:плат\w*|вознагражден\w*|тариф\w*)
procedure~platform_access~Условия доступа к электронной площадке~аккредитаци~(?:аккредитаци\w*|регистраци\w*).{0,115}?(?:электронн\w*\s+площадк\w*|оператор\w*\s+этп)
procedure~paper_bids~Необоснованная бумажная процедура~бумажн~(?:бумажн\w*\s+форм\w*|бумажн\w*\s+заявк\w*).{0,120}?(?:конкурентн\w*\s+закупк\w*|торг\w*)
procedure~multiple_offers~Требование нескольких ценовых предложений~ценов~(?:ценов\w*\s+предложени\w*).{0,115}?(?:нескольк\w*\s+вариант\w*|не\s+менее\s+двух\s+вариант\w*)
procedure~negotiations~Переговоры после подачи конкурентных заявок~переговор~(?:переговор\w*).{0,115}?(?:после\s+подачи\s+заявк\w*|после\s+вскрыти\w*|с\s+отдельн\w*\s+участник\w*)
procedure~reauction~Переторжка и повторные ценовые предложения~переторж~(?:переторжк\w*|повторн\w*\s+ценов\w*\s+предложени\w*)
procedure~auction_step~Шаг аукциона и порядок торгов~шаг~(?:шаг\s+аукциона|порядок\s+проведени\w*\s+аукциона)
procedure~auction_end~Досрочное завершение аукциона~аукцион~аукцион\w*.{0,150}?(?:досрочн\w*\s+заверш\w*|по\s+решени\w*\s+заказчик\w*)
procedure~closed_procurement~Основания закрытой конкурентной закупки~закрыт~(?:закрыт\w*\s+конкурс\w*|закрыт\w*\s+аукцион\w*|закрыт\w*\s+конкурентн\w*\s+закупк\w*)
procedure~negotiated_single~Прямой договор после несостоявшейся процедуры~несостоявш~(?:несостоявш\w*\s+конкурентн\w*\s+закупк\w*).{0,125}?единственн\w*\s+поставщик\w*
evaluation~scoring_criteria~Наличие измеримых критериев оценки~критери~(?:критери\w*\s+оценк\w*|показател\w*\s+оценк\w*).{0,130}?(?:балл\w*|значимост\w*|вес\w*)
evaluation~scoring_weights~Сумма значимостей критериев и методика~значимост~(?:значимост\w*\s+критери\w*|вес\w*\s+критери\w*).{0,110}?(?:процент\w*|балл\w*|\d+\s*%)
evaluation~committee_discretion~Произвольная оценка комиссией~комисси~(?:оценк\w*\s+заявк\w*|определени\w*\s+победител\w*).{0,100}?по\s+усмотрени\w*\s+комисси\w*
evaluation~hidden_rules~Неопубликованные основания отклонения~отклон~(?:основани\w*\s+отклонени\w*|причин\w*\s+отклонени\w*).{0,125}?(?:определ\w*\s+позднее|по\s+усмотрени\w*)
evaluation~minimum_score~Минимальный проходной балл заявки~балл~(?:минимальн\w*\s+балл\w*|порог\w*\s+оценк\w*).{0,105}?(?:заявк\w*|участник\w*)
evaluation~tie_breaker~Порядок определения победителя при равенстве цен~равенств~(?:равенств\w*\s+цен\w*|одинаков\w*\s+предложени\w*).{0,120}?(?:победител\w*|заявк\w*)
evaluation~extreme_bid~Антидемпинговые меры и обоснование цены~демпинг~(?:демпинг\w*|антидемпинг\w*).{0,110}?(?:обеспечени\w*|цен\w*|снижен\w*)
evaluation~bid_rejection~Отклонение заявки и обоснование~отклон~(?:отклонени\w*\s+заявк\w*|заявк\w*\s+отклон\w*).{0,130}?(?:основани\w*|причин\w*|протокол\w*)
evaluation~committee_conflict~Конфликт интересов членов комиссии~конфликт~(?:конфликт\w*\s+интересов|заинтересованност\w*\s+член\w*\s+комисси\w*)
evaluation~qualifications~Несоразмерный опыт аналогичных контрактов~опыт~(?:опыт\w*\s+исполнени\w*\s+договор\w*|аналогичн\w*\s+договор\w*).{0,135}?(?:не\s+менее|только|исключительно)
protocols~opening_protocol~Протокол вскрытия заявок~протокол~протокол\s+(?:вскрыти\w*|открыти\w*)\s+(?:конверт\w*|заявк\w*)
protocols~application_protocol~Протокол рассмотрения и допуска заявок~протокол~протокол\s+рассмотрени\w*\s+заявк\w*
protocols~final_protocol~Итоговый протокол закупки~протокол~(?:итогов\w*\s+протокол\w*|протокол\s+подведени\w*\s+итог\w*)
protocols~protocol_publication~Размещение протокола в ЕИС~протокол~протокол\w*.{0,120}?(?:опубликован\w*\s+в\s+еис|размещен\w*\s+в\s+еис)
protocols~protocol_revisions~Протокол отмены или исправления предыдущего протокола~отмен~(?:протокол\w*\s+об\s+отмене|отмен\w*\s+протокол\w*|изменени\w*\s+протокол\w*)
protocols~protocol_no_reasons~Протокол без причины отклонения заявки~протокол~протокол\w*.{0,150}?(?:причин\w*\s+отклонени\w*\s+не\s+указан\w*|основани\w*\s+не\s+приведен\w*)
protocols~final_winner~Сведения о победителе и условиях предложения~победител~(?:победител\w*|лучше\w*\s+предложени\w*).{0,125}?(?:протокол\w*|цен\w*\s+предложени\w*)
protocols~cancellation_protocol~Документы отмены закупки~отмен~(?:протокол\w*\s+отмен\w*|решени\w*\s+об\s+отмене\s+закупк\w*)
contract~contract_signing_period~Срок заключения договора по итогам закупки~заключени~(?:срок\w*\s+заключени\w*\s+договора|договор\s+заключа\w*).{0,115}?(?:дней|рабоч\w*\s+дн\w*|после\s+протокол\w*)
contract~contract_difference~Отступление договора от проекта документации~проект~(?:проект\w*\s+договора|заключаем\w*\s+договор\w*).{0,145}?(?:услови\w*\s+изменен\w*|существенн\w*\s+услови\w*)
contract~contract_unilateral~Одностороннее изменение договора заказчиком~односторон~(?:заказчик\w*|договор\w*).{0,130}?в\s+односторонн\w*\s+порядке.{0,85}?(?:изменить|расторгнуть)
contract~contract_payment_delay~Длительный срок оплаты по договору~оплат~(?:срок\s+оплат\w*|расчет\w*\s+производ\w*).{0,120}?\d{2,3}\s+(?:рабоч\w*|календарн\w*)\s+дн
contract~contract_no_deadline~Неопределённый срок оплаты~оплат~(?:срок\s+оплат\w*|оплат\w*\s+производ\w*).{0,90}?(?:по\s+усмотрени\w*\s+заказчик\w*|после\s+наличи\w*\s+средств)
contract~contract_security~Обеспечение исполнения договора отдельно от заявки~обеспечени~обеспечени\w*\s+исполнени\w*\s+договора.{0,130}?(?:устанавлива\w*|размер\w*|составля\w*)
contract~contract_guarantee_beneficiary~Условия независимой гарантии для исполнения договора~гаранти~независим\w*\s+гаранти\w*.{0,140}?(?:бенефициар\w*|гарант\w*|банк\w*)
contract~contract_advance~Аванс и порядок возврата авансов~аванс~(?:аванс\w*|авансировани\w*).{0,120}?(?:возврат\w*|обеспечени\w*|срок\w*)
contract~contract_liability~Ответственность сторон и неустойка~неустойк~(?:неустойк\w*|штраф\w*|пен\w*).{0,110}?(?:договора|поставщик\w*|заказчик\w*)
contract~contract_acceptance~Порядок приёмки товара или работ~приемк~(?:приемк\w*|приёмк\w*).{0,120}?(?:акт\w*|срок\w*|мотивированн\w*\s+отказ\w*)
contract~contract_subcontract~Запрет или ограничения субподряда~субподряд~(?:запреща\w*|не\s+допуска\w*).{0,100}?(?:субподрядчик\w*|соисполнител\w*)
contract~contract_change_volume~Изменение объёма исполнения после подписания~объем~(?:изменени\w*\s+объем\w*|увеличени\w*\s+объем\w*).{0,125}?(?:по\s+соглашени\w*|по\s+решени\w*)
contract~contract_publication~Публикация заключённого договора в ЕИС~договор~(?:заключенн\w*\s+договор\w*|сведения\s+о\s+договор\w*).{0,130}?(?:размещен\w*\s+в\s+еис|включен\w*\s+в\s+реестр\w*)
contract~contract_termination~Расторжение договора и сведения в реестре~расторжен~(?:расторжени\w*\s+договора|договор\w*\s+расторгнут\w*).{0,125}?(?:реестр\w*|еис|информаци\w*)
contract~contract_stage~Исполнение договора по этапам~этап~(?:этап\w*\s+исполнени\w*|поэтапн\w*\s+исполнени\w*).{0,95}?(?:договор\w*|оплат\w*)
national~ban_goods~Запрет закупки иностранных товаров~запрет~(?:запрет\w*\s+закупк\w*|запрет\s+допуск\w*).{0,115}?иностранн\w*\s+товар\w*
national~restriction_goods~Ограничение закупки иностранных товаров~ограничен~ограничени\w*\s+закупк\w*.{0,120}?иностранн\w*\s+товар\w*
national~advantage_goods~Преимущество российским товарам~преимуществ~(?:преимуществ\w*\s+российск\w*\s+товар\w*|приоритет\w*\s+товар\w*\s+российск\w*)
national~okpd2_mismatch~Сопоставление ОКПД2 с перечнями национального режима~окпд~(?:окпд2|окпд).{0,150}?(?:запрет\w*|ограничени\w*|национальн\w*\s+режим\w*)
national~country_declaration~Документальное подтверждение страны происхождения~происхожд~(?:стран\w*\s+происхождени\w*|российск\w*\s+происхождени\w*).{0,145}?(?:деклараци\w*|реестр\w*|подтверждени\w*)
national~registry_confirmation~Реестровые номера российской продукции~реестров~(?:реестров\w*\s+номер\w*|реестр\w*\s+российск\w*\s+промышленн\w*).{0,150}?(?:товар\w*|продукци\w*)
national~exception_1875~Проверка исключений из постановления № 1875~исключени~(?:исключени\w*\s+из\s+национальн\w*\s+режим\w*|исключени\w*\s+из\s+запрет\w*).{0,145}?(?:постановлени\w*|п\s*1875|закупк\w*)
national~minpromtorg_authorization~Разрешение Минпромторга на иностранный товар~минпромторг~разрешени\w*\s+минпромторг\w*.{0,130}?(?:иностранн\w*\s+товар\w*|закупк\w*)
national~eaeu_production~Применение правил к товарам ЕАЭС~еаэс~(?:еаэс|евразийск\w*\s+экономическ\w*\s+союз\w*).{0,120}?(?:товар\w*|страна\w*|происхождени\w*)
national~offer_rejection~Отклонение иностранных предложений~иностранн~(?:иностранн\w*\s+товар\w*|иностранн\w*\s+происхождени\w*).{0,140}?(?:отклоня\w*|приравнив\w*|ограничива\w*)
national~lot_mix~Смешанный лот и национальный режим~лот~(?:лот\w*|лот\s+№).{0,115}?(?:российск\w*\s+товар\w*|иностранн\w*\s+товар\w*)
national~unreliable_origin~Противоречия в документах о происхождении~происхожд~(?:сведени\w*\s+о\s+происхождени\w*|страна\s+происхождени\w*).{0,140}?(?:противореч\w*|не\s+соответств\w*)
reporting~monthly_report~Ежемесячная отчётность о закупках~ежемесячн~(?:ежемесячн\w*\s+отчет\w*|отчет\w*\s+за\s+месяц).{0,135}?(?:закупк\w*|договор\w*)
reporting~annual_sme_report~Годовой отчёт о закупках МСП~годов~(?:годов\w*\s+отчет\w*).{0,140}?мсп
reporting~national_regime_report~Отчёт о закупках российских товаров~отчет~(?:отчет\w*\s+об\s+объеме\s+закупок|отчет\w*\s+российск\w*\s+товар\w*)
reporting~registry_contracts~Реестр договоров по 223-ФЗ~реестр~реестр\s+договор\w*.{0,120}?(?:еис|закупк\w*|заказчик\w*)
reporting~closed_secret~Законность неразмещения закрытых сведений~закрыт~(?:закрыт\w*\s+закупк\w*|нераскрыти\w*\s+информаци\w*).{0,130}?(?:гостайн\w*|решени\w*\s+правительств\w*|секретн\w*)
reporting~complaints_fas~Жалобы на действия заказчика~жалоб~(?:жалоб\w*\s+в\s+фас|жалоб\w*\s+на\s+действи\w*\s+заказчик\w*|рассмотрени\w*\s+жалоб\w*)
reporting~unfair_suppliers~Реестр недобросовестных поставщиков~недобросовест~реестр\s+недобросовестн\w*\s+поставщик\w*
reporting~control_order~Предписание контрольного органа~предписани~(?:предписани\w*\s+фас|предписани\w*\s+контрольн\w*\s+орган\w*)
reporting~no_info_reason~Правовые основания непубликации информации~не\s+размещ~(?:информаци\w*|закупк\w*).{0,95}?(?:не\s+размеща\w*\s+в\s+еис|не\s+подлежит\s+размещени\w*)
reporting~special_rules~Особые правила отдельных категорий заказчиков~специальн~(?:специальн\w*\s+правил\w*|особ\w*\s+поряд\w*).{0,100}?(?:заказчик\w*|закупк\w*)"""

def parse_catalog(source=RAW):
    output=[]
    codes=set()
    for line in source.splitlines():
        if not line.strip():continue
        parts=line.strip().split("~")
        if len(parts)!=5:
            raise ValueError("Invalid legal rule fields")
        group,code,title,anchor,pattern=parts
        if group not in BASIS or code in codes or not anchor:
            raise ValueError("Invalid or repeated legal rule: "+code)
        compiled=re.compile(pattern,re.IGNORECASE|re.DOTALL)
        if compiled.match("") is not None:
            raise ValueError("Zero-width legal rule: "+code)
        codes.add(code)
        output.append({
            "group":group,"code":code,"title":title,"law":BASIS[group],
            "anchor":anchor.casefold().replace("ё","е"),
            "pattern":compiled,
            "type":"review_if_explicit_document_evidence"
        })
    return tuple(output)

CATALOG=parse_catalog()
CHAPTERS={
 "regulation":"Положение о закупке и сфера применения",
 "planning":"Планы закупок и изменения",
 "notices":"Извещение и данные ЕИС",
 "docs":"Документация, лоты и техническое задание",
 "dates":"Сроки, разъяснения, изменения и отмена",
 "access":"Участники и ограничения конкуренции",
 "application":"Требования к заявке",
 "security":"Обеспечение заявок и исполнения",
 "sme":"Закупки для субъектов МСП",
 "procedure":"Электронные и закрытые процедуры",
 "evaluation":"Рассмотрение, оценка и допуск",
 "protocols":"Протоколы и результаты",
 "contract":"Заключение, исполнение, оплата и реестр",
 "national":"Национальный режим",
 "reporting":"Отчётность, контроль, исключения"
}

# Registry-only or comparative checks must NEVER be fabricated from an absent
# word in a document; show these as requiring independent official evidence.
STRUCTURAL = (
 ("regulation","regulation_approval","Дата утверждения положения и 15-дневное размещение","223-ФЗ, ст. 4 ч. 1","EIS publication event + approval"),
 ("planning","planning_publication","Своевременность размещения годового плана","223-ФЗ, ст. 4 ч. 2–3","Official plan/revision archive"),
 ("planning","planning_procurement_link","Соответствие позиции плана и закупки","223-ФЗ, ст. 4 ч. 2; ПП РФ № 932","Plan position + notice"),
 ("planning","planning_exception","Применимость исключения из планирования","223-ФЗ, ст. 4 ч. 15–16","Statutory exception and value"),
 ("notices","notice_actual_publish","Дата первого размещения извещения в ЕИС","223-ФЗ, ст. 4 ч. 9","EIS notice revision event"),
 ("notices","notice_source_url","Существующая официальная ссылка ЕИС","223-ФЗ, ст. 4","Valid official registry URL"),
 ("notices","notice_price_consistency","Цена НМЦД, максимальное значение и формула","223-ФЗ, ст. 4 ч. 9–10","Verified original XML + documentation"),
 ("docs","all_attachments_read","Читаемость каждого приложения и версии","223-ФЗ, ст. 4 ч. 8–10","Stored document receipts and SHA256"),
 ("docs","notice_docs_matching","Построчная сверка извещения и документации","223-ФЗ, ст. 4 ч. 8","Matching versions of originals"),
 ("dates","request_response_3workdays","Ответ на разъяснение в течение трёх рабочих дней","223-ФЗ, ст. 3.2 ч. 3","Dated question + response + calendar"),
 ("dates","clarification_3workdays_cutoff","Право не отвечать на поздний запрос","223-ФЗ, ст. 3.2 ч. 3","Request date + bid deadline + calendar"),
 ("dates","amendment_3days","Размещение изменения в течение трёх дней","223-ФЗ, ст. 4 ч. 11","Adoption and EIS publication dates"),
 ("dates","amendment_half_term","Продление срока подачи после изменения","223-ФЗ, ст. 4 ч. 11","Current tender method + regulation + EIS revisions"),
 ("dates","cancellation_publication","Дата решения и размещения отмены","223-ФЗ, ст. 3.2 ч. 5–7","Official cancellation protocol and EIS"),
 ("dates","publication_days_only","Сравнение календарных дат без времени","223-ФЗ, ст. 3.2; ст. 4","Verified date fields"),
 ("procedure","minimum_bid_period_by_method","Минимальный срок подачи по виду процедуры","223-ФЗ, ст. 3.2, 3.4","Method, scope, dates and calendar"),
 ("sme","sme_status_from_official","Подтверждение режима МСП из официальных сведений","223-ФЗ, ст. 3.4; ПП РФ № 1352","Notice registry + SME designation"),
 ("sme","sme_price_limits","Предельные НМЦД способов закупок МСП","223-ФЗ, ст. 3.4 ч. 3","Method + verified price + SME designation"),
 ("sme","sme_payment_7workdays","Срок оплаты МСП с учётом исключений","ПП РФ № 1352","Contract terms + SME status + exceptions"),
 ("sme","sme_annual_share","Годовые нормативы закупок у МСП","ПП РФ № 1352","Annual reports and coverage"),
 ("security","security_bid_5million_gate","Запрет обеспечения заявки при НМЦД до 5 млн","223-ФЗ, ст. 3.2 ч. 27","Verified price + affirmative bid-security clause"),
 ("security","security_bid_percent_gate","Проверка предельной доли обеспечения заявки","223-ФЗ, ст. 3.2 ч. 27","Verified bid security amount + price"),
 ("security","security_contract_separated","Разграничение обеспечения заявки и договора","223-ФЗ, ст. 3.2 ч. 25–27","Context of security clause"),
 ("evaluation","final_protocol_eis_time","Срок размещения итогового протокола","223-ФЗ, ст. 4 ч. 12","Signed protocol + EIS event"),
 ("contract","contract_registry_3workdays","Срок включения сведений в реестр договоров","223-ФЗ, ст. 4.1; ПП РФ № 1132","Contract date + register event"),
 ("contract","contract_payment_exceptions","Исключения сроков оплаты товаров и работ","223-ФЗ, ст. 3 ч. 5.3","Contract type + exception + terms"),
 ("contract","post_award_changes","Правомерность изменения условий заключённого договора","223-ФЗ, ст. 4.1","Contract changes + register history"),
 ("national","national_okpd2_scope","Применимость перечней национального режима","223-ФЗ, ст. 3.1-4; ПП РФ № 1875","OKPD2 code + dated Annex lists"),
 ("national","national_exceptions","Исключения национального режима и разрешения","ПП РФ № 1875","Product codes + exemptions + permits"),
 ("national","national_eaeu_2026","Учёт актуальных исключений ЕАЭС","ПП РФ № 1875 (редакция по дате закупки)","Notice date + country + product classification"),
 ("national","national_annual_report","Отчёт об объёме закупок российской продукции","ПП РФ № 1875","Annual official reports"),
 ("reporting","monthly_eis_report_dates","Своевременность ежемесячного отчёта","223-ФЗ, ст. 4 ч. 19","Official EIS publication dates"),
 ("reporting","annual_sme_report_dates","Срок публикации отчёта о закупках МСП","223-ФЗ, ст. 4 ч. 21; ПП РФ № 1352","Official SME annual report"),
 ("reporting","closed_procurement_exception","Законное основание для закрытого размещения","223-ФЗ, ст. 3.5, ст. 4 ч. 15–16","Government act + notice type"),
 ("reporting","rnp_inclusion","Основания включения в РНП","223-ФЗ, ст. 5","Regulator decision + bid and contract records"),
 ("reporting","fas_prescription","Исполнение предписаний контрольного органа","223-ФЗ, ст. 6; 135-ФЗ, ст. 18.1","Verified regulator disposition"),
)

MAX_DOCS=300
MAX_CHARS_PER_DOC=400000
MAX_NEW_SIGNALS_PER_AUDIT=100
MAX_PER_CATEGORY=2
# Never allow these generic qualifiers to be interpreted as binding tender
# requirements. The snippets remain contextual review items regardless.
NEGATIVE=re.compile(
    r"(?:не\s+требует\w*|не\s+устанавлива\w*|не\s+предусматрива\w*|"
    r"не\s+применя\w*|требовани\w*\s+не\s+предъявля\w*|не\s+взима\w*)",re.I)

def _slim(s):
    return " ".join(str(s or "").split())

def signals(p,docs):
    out=[]
    count={}
    seen=set()
    active=CATALOG
    for doc in docs[:MAX_DOCS]:
        if not isinstance(doc,dict) or doc.get("status")!="checked":continue
        text=str(doc.get("text") or "")[:MAX_CHARS_PER_DOC].replace("\xa0"," ")
        if len(text.strip())<25:continue
        folded=text.casefold().replace("ё","е")
        for rule in active:
            if len(out)>=MAX_NEW_SIGNALS_PER_AUDIT:return out
            code=rule["code"]
            if count.get(code,0)>=MAX_PER_CATEGORY:continue
            if rule["anchor"] not in folded:continue
            m=rule["pattern"].search(text)
            if not m:continue
            # Evaluate the matching sentence, not a wide context that may
            # accidentally swallow unrelated negative expressions.
            sentence=text[max(0,text.rfind(".",0,m.start())+1):min(len(text),m.end()+100)]
            if NEGATIVE.search(sentence) and not re.search(r"не\s+(?:соответств\w*|допуска\w*)",m.group(0),re.I):
                continue
            excerpt=_slim(text[max(0,m.start()-100):min(len(text),m.end()+180)])[:550]
            signature=(code,str(doc.get("name") or ""),_slim(m.group(0)).casefold())
            if signature in seen:continue
            seen.add(signature)
            count[code]=count.get(code,0)+1
            out.append({
                "scope":"223-ФЗ / полный каталог / "+CHAPTERS[rule["group"]],
                "item":rule["title"],"status":"check",
                "evidence":excerpt,"source":doc.get("name"),
                "source_url":(doc.get("meta") or {}).get("source_url") or doc.get("url") or "",
                "document":doc.get("name"),
                "field_path":"v337-"+code,
                "law":rule["law"],"law_url":"https://www.consultant.ru/document/cons_doc_LAW_116964/",
                "location":"Документ: "+str(doc.get("name") or "")+", позиция "+str(m.start()+1),
                "note":("Выявлен текстовый признак для проверки, не установленное нарушение. "
                        "Оригинал ЕИС подтвержден: "+("да" if verified(doc) else "нет")+
                        ". Для квалификации проверить актуальное положение о закупке, "
                        "действующую редакцию документов, применимость требований и исключения.")
            })
    return out

def list_categories():
    active=[{
        "id":"v337-"+r["code"],
        "group":r["group"],
        "section":CHAPTERS[r["group"]],
        "title":r["title"],"law":r["law"],
        "qualification":"review_on_document_match"
    } for r in CATALOG]
    structural=[{
        "id":"v337-"+code,"group":group,"section":CHAPTERS[group],
        "title":title,"law":law,
        "qualification":"requires_external_or_structured_evidence",
        "required_evidence":source
    } for group,code,title,law,source in STRUCTURAL]
    legacy=[{
        "id":"v331-"+code,"group":"prior_rules","title":title,"law":law,
        "qualification":"existing_review_rule"
    } for code,title,description,law,patterns in OLD_CATALOG]
    matrix=[{
        "id":"v336-"+code,"group":"prior_rules","title":title,"law":law,
        "qualification":"existing_review_rule"
    } for code,title,law,pattern in V336_CATEGORIES]
    return {"total":len(active)+len(structural)+len(legacy)+len(matrix),
            "new_text_categories":len(active),
            "new_structural_categories":len(structural),
            "existing_text_categories":len(legacy)+len(matrix),
            "chapters":len(CHAPTERS),
            "status_note":"Category availability is not proof of infringement.",
            "items":active+structural+legacy+matrix}

def install(ns):
    old=ns.get("build_checks")
    app=ns["app"]
    if not callable(old):raise RuntimeError("V337 needs build_checks")
    if getattr(old,"__v337__",False):return
    @wraps(old)
    def build_checks(p,detail,card,docs):
        return list(old(p,detail,card,docs))+signals(p,docs)
    build_checks.__v337__=True
    ns["build_checks"]=build_checks
    @app.get("/api/v337/legal-categories",include_in_schema=False)
    def legal_categories():
        return list_categories()
    print("V337 ALL_CATEGORIES_READY "+str(len(CATALOG))+
          " text="+str(len(CATALOG))+" structural="+str(len(STRUCTURAL))+
          " total="+str(list_categories()["total"])+" review_only=True",flush=True)
