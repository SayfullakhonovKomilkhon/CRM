import asyncio

from sqlalchemy import select

from crm.database import SessionLocal
from crm.models import Client, Project, Role, User
from crm.security import hash_password

DEMO_USERS = [
    ("admin@crm.local", "Администратор", Role.ADMIN),
    ("manager@crm.local", "Менеджер сценаристов", Role.MANAGER),
    ("editor-manager@crm.local", "Менеджер монтажёров", Role.EDITOR_MANAGER),
    (
        "publisher-manager@crm.local",
        "Менеджер публицистов",
        Role.PUBLISHER_MANAGER,
    ),
    ("scenarist@crm.local", "Сценарист", Role.SCENARIST),
    ("editor@crm.local", "Монтажёр", Role.EDITOR),
    ("client@crm.local", "Клиент", Role.CLIENT),
    ("publisher@crm.local", "Публицист", Role.PUBLISHER),
]


async def seed() -> None:
    async with SessionLocal() as session:
        demo_client = await session.scalar(select(Client).where(Client.name == "Демо-клиент"))
        if demo_client is None:
            demo_client = Client(name="Демо-клиент", external_id="demo-client")
            session.add(demo_client)
            await session.flush()

        demo_project = await session.scalar(
            select(Project).where(
                Project.client_id == demo_client.id, Project.name == "Демо-проект"
            )
        )
        if demo_project is None:
            session.add(Project(client_id=demo_client.id, name="Демо-проект"))

        for email, full_name, role in DEMO_USERS:
            existing = await session.scalar(select(User).where(User.email == email))
            if existing is None:
                existing = User(
                    email=email,
                    full_name=full_name,
                    role=role,
                    password_hash=hash_password("demo12345"),
                )
                session.add(existing)
            if role == Role.CLIENT:
                existing.client_id = demo_client.id
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
