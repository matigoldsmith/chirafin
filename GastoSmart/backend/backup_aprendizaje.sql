PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE aprendizaje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patron TEXT UNIQUE NOT NULL,
            comercio_limpio TEXT NOT NULL,
            categoria_fija TEXT
        , es_recibo_fijo INTEGER, monto_fijo REAL, fecha_fija TEXT);
INSERT INTO aprendizaje VALUES(98,'marclau com de combust','Marclau com de combust','Combustible',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(99,'instituto hebreo','Instituto hebreo','Educación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(102,'comunidad raíces del taihuen 12.600','Comunidad raíces del taihuen 12.600','Gastos Comunes',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(103,'restaurante frb eireli','Restaurante frb eireli','Alimentación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(104,'nueva ssi sa','Nueva ssi sa','Salud',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(105,'farmacias cruz verde','Farmacias cruz verde','Farmacia',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(108,'point do forno','Point do forno','Alimentación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(111,'oxxo','Oxxo','Alimentación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(112,'castro preisler spa (enex)','Castro preisler spa (enex)','Alimentación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(113,'maria maria gelato','Maria maria gelato','Alimentación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(115,'nueva ssi s.a.','Nueva ssi s.a.','Salud',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(116,'esmax red limitada','Esmax red limitada','Alimentación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(117,'ripley','Ripley','Vestuario y Calzado',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(118,'colegio hebreo dr. chaim weizmann','Colegio hebreo dr. chaim weizmann','Educación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(119,'copec (pronto)','Copec (pronto)','Alimentos y bebidas',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(120,'pestana south beach hotel','Pestana south beach hotel','Viajes y Alojamiento',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(121,'cencosud shopping s.a. - estacionamiento portal la dehesa','Cencosud shopping s.a. - estacionamiento portal la dehesa','Estacionamiento',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(748,'lounge rua das pedras ltda me','Lounge rua das pedras ltda me','Alimentación y Entretenimiento',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(750,'real food elm','Real food elm','Alimentación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(753,'cencosud shopping s.a. (estacionamiento portal la dehesa)','Cencosud shopping s.a. (estacionamiento portal la dehesa)','Gastos de Estacionamiento',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(756,'sociedad inversiones ceron spa','Sociedad inversiones ceron spa','Restaurantes y alimentación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(759,'copec','Copec','Combustible',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(760,'laranjinha','Laranjinha','Gastos generales',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(761,'castaño','Castaño','Alimentación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(763,'jumbo','Jumbo','Supermercado',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(770,'bimba y lola','Bimba y lola','Vestuario y Accesorios',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(771,'fraccional spa','Fraccional spa','Gastos financieros / Comisiones',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(1617,'pronto','Pronto','Representación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(1618,'adrenalina','Adrenalina','Representación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(1619,'starbucks','Starbucks','Representación',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(1620,'granada park ñuñoa','Granada Park','Viajes / Movilización',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(1621,'buscalibre','Buscalibre','Material de lectura / Libros',1,NULL,NULL);
INSERT INTO aprendizaje VALUES(1622,'jumbo la dehesa (sasec chile)','Jumbo La Dehesa (Sasec Chile)','Lavandería y Tintorería',1,NULL,NULL);
COMMIT;
