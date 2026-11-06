# bazaar-contrib-server-github

Logging in to the Bazaar Compute server with a GitHub account.

```toml
[[contribs]]
package = "bazaar-contrib-server-github~=0.3.0rc1"
[[contribs.enabled]]
entry = "server-github"
```

Create an OAuth App on GitHub, then fill in its Client ID and Client Secret under Settings › Login methods, where the callback URL to give the app is shown.
