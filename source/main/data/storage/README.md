# Folder `storage`

Handles reading from and writing to persistent storage. As of now, the following data is stored:

- **JSON Dictionary**: Caches the latest sitemap data for use on app startup. It is stored together with a timestamp that determines whether the data is still considered fresh. The timestamp is refreshed whenever a response identical to the cached JSON is received.
- **Sitemap Label**: Stored for display by the _Glance_.

## Further Reading

- [Connect IQ Core Topics – Persisting Data](https://developer.garmin.com/connect-iq/core-topics/persisting-data/)
- [Connect IQ API Docs – `Toybox.Application.Storage`](https://developer.garmin.com/connect-iq/api-docs/Toybox/Application/Storage.html)
