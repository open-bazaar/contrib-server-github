"""What the GitHub login keeps: its one set of settings, and which GitHub user
each account came from, with the token renewing the account's sessions."""

import bazaar_contrib_storage as s
from bazaar_contrib_schema import Migration, ModuleSchema

SETTINGS = s.Table(
    name="github_settings",
    columns=(
        s.ColumnDefinition(
            name="id",
            type="integer",
            check=s.Compare(
                left=s.Column(name="id"), operator="=", right=s.Value(value=1)
            ),
        ),
        s.ColumnDefinition(name="client_id", type="text"),
        s.ColumnDefinition(name="client_secret", type="text"),
        s.ColumnDefinition(name="redirect_uri", type="text"),
        s.ColumnDefinition(name="default_role_id", type="text", nullable=True),
        s.ColumnDefinition(name="session_minutes", type="integer", nullable=True),
        s.ColumnDefinition(name="updated_at_ms", type="integer"),
    ),
    primary_key=("id",),
)
ACCOUNTS = s.Table(
    name="github_accounts",
    columns=(
        s.ColumnDefinition(name="github_id", type="text"),
        s.ColumnDefinition(name="account_id", type="text", unique=True),
        s.ColumnDefinition(name="login", type="text"),
        s.ColumnDefinition(name="name", type="text"),
        s.ColumnDefinition(name="email", type="text", nullable=True),
        s.ColumnDefinition(name="avatar_url", type="text", nullable=True),
        s.ColumnDefinition(name="token", type="text"),
        s.ColumnDefinition(name="refresh_token", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer"),
        s.ColumnDefinition(name="updated_at_ms", type="integer"),
    ),
    primary_key=("github_id",),
)

MODULE = ModuleSchema(
    tables=("github_settings", "github_accounts"),
    migrations=(
        Migration(
            version=1,
            name="github_login",
            operations=(s.CreateTable(table=SETTINGS), s.CreateTable(table=ACCOUNTS)),
        ),
    ),
)
