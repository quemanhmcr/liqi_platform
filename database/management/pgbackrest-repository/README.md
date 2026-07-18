# Independent pgBackRest repository provider

This provider runs on independent management/storage hardware, never on the OCI application host. It exposes pgBackRest TLS server port 8432 only on the WireGuard overlay, requires a client certificate CN authorized solely for stanza `liqi`, and stores encrypted backup/WAL data under `/independent-storage/pgbackrest/liqi`.

The repository host owns filesystem capacity, retention monitoring, encrypted storage backups and restore tests. The OCI host owns PostgreSQL authority and invokes pgBackRest as a TLS client. Neither side uses OCI Object Storage, S3 APIs or AWS-style Customer Secret Keys.

Required bootstrap order:

1. create the dedicated `pgbackrest` account and independent filesystem;
2. provision a private CA or approved internal CA, server certificate/key and application-host client certificate/key;
3. establish WireGuard and bind the TLS server only to its private overlay address;
4. render the server config, validate it with `pgbackrest --config=... check`, then enable the service;
5. publish checksummed capacity evidence with `report-capacity.py` for the exact source SHA;
6. back up this repository filesystem to another independent encrypted medium and prove restore before production cutover.

No private key or cipher passphrase belongs in Git, command-line arguments, OCI user-data, an OpenTofu plan or the application-host artifact archive.
