import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from vidxp.core.people import PersonClusterLink, PersonRecord, PersonReference
from vidxp.infrastructure.local_catalog import LocalCatalog


def _uuid() -> str:
    return uuid4().hex


def person_record(
    person_id: str | None = None,
    *,
    display_name: str = "Jane Doe",
    notes: str | None = None,
    biography: str | None = None,
) -> PersonRecord:
    return PersonRecord(
        person_id=person_id or _uuid(),
        display_name=display_name,
        notes=notes,
        biography=biography,
        created_at=datetime.now(timezone.utc),
    )


def cluster_link(
    *,
    person_id: str,
    cluster_id: str,
    media_id: str,
    generation_id: str,
) -> PersonClusterLink:
    return PersonClusterLink(
        person_id=person_id,
        cluster_id=cluster_id,
        media_id=media_id,
        generation_id=generation_id,
        created_at=datetime.now(timezone.utc),
    )


def person_reference(
    *,
    person_id: str,
    reference_id: str | None = None,
) -> PersonReference:
    checksum = "1" * 64
    return PersonReference(
        reference_id=reference_id or _uuid(),
        person_id=person_id,
        storage_key=f"objects/{checksum[:2]}/{checksum}.jpg",
        sha256=checksum,
        byte_size=100,
        mime_type="image/jpeg",
        created_at=datetime.now(timezone.utc),
    )


class PeopleCatalogTests(unittest.TestCase):
    def test_catalog_creates_people_tables_and_bumps_schema_version(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "catalog.sqlite3"
            catalog = LocalCatalog(database)
            with catalog.engine.connect() as connection:
                tables = set(
                    connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table'"
                    ).scalars()
                )
                self.assertTrue(
                    {
                        "people",
                        "person_aliases",
                        "person_references",
                        "person_cluster_links",
                    }.issubset(tables)
                )
                self.assertEqual(
                    connection.exec_driver_sql(
                        "SELECT schema_version FROM catalog_metadata"
                    ).scalar_one(),
                    5,
                )

    def test_put_and_get_person_round_trips(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record(display_name="Alex Rivera", notes="met at conference")
            self.assertEqual(catalog.put_person(record), record)
            self.assertEqual(catalog.get_person(record.person_id), record)
            self.assertIsNone(catalog.get_person(_uuid()))

    def test_put_person_is_idempotent_for_identical_record(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record()
            self.assertEqual(catalog.put_person(record), record)
            self.assertEqual(catalog.put_person(record), record)

    def test_put_person_rejects_conflicting_record_for_same_id(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record()
            catalog.put_person(record)
            conflicting = person_record(
                record.person_id,
                display_name="Someone Else",
            )
            with self.assertRaises(FileExistsError):
                catalog.put_person(conflicting)

    def test_list_people_returns_all_created_records(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            first = person_record(display_name="Person One")
            second = person_record(display_name="Person Two")
            catalog.put_person(first)
            catalog.put_person(second)
            listed = catalog.list_people(limit=10)
            self.assertEqual(set(p.person_id for p in listed), {first.person_id, second.person_id})

    def test_aliases_can_be_added_removed_and_listed(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record()
            catalog.put_person(record)

            catalog.add_alias(record.person_id, "AR")
            catalog.add_alias(record.person_id, "Al")
            self.assertEqual(
                set(catalog.list_aliases(record.person_id)),
                {"AR", "Al"},
            )

            catalog.remove_alias(record.person_id, "AR")
            self.assertEqual(catalog.list_aliases(record.person_id), ("Al",))

    def test_adding_the_same_alias_twice_does_not_error(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record()
            catalog.put_person(record)
            catalog.add_alias(record.person_id, "AR")
            catalog.add_alias(record.person_id, "AR")  # must not raise
            self.assertEqual(catalog.list_aliases(record.person_id), ("AR",))

    def test_references_can_be_added_listed_and_removed(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record()
            catalog.put_person(record)

            reference = person_reference(person_id=record.person_id)
            catalog.add_reference(reference)
            listed = catalog.list_references(record.person_id)
            self.assertEqual(listed, (reference,))

            catalog.remove_reference(reference.reference_id)
            self.assertEqual(catalog.list_references(record.person_id), ())

    def test_cluster_links_survive_reindexing_with_a_new_generation(self):
        """The core requirement of Issue #85: a reviewed person and its
        links must survive re-indexing, even though re-indexing produces
        a brand new anonymous cluster_id under a new generation_id.
        """
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record()
            catalog.put_person(record)

            media_id = _uuid()
            generation_a = _uuid()
            generation_b = _uuid()

            link_before_reindex = cluster_link(
                person_id=record.person_id,
                cluster_id="cluster-a",
                media_id=media_id,
                generation_id=generation_a,
            )
            catalog.link_cluster(link_before_reindex)

            # Simulate re-indexing: a new generation produces a
            # different anonymous cluster id for the same person.
            link_after_reindex = cluster_link(
                person_id=record.person_id,
                cluster_id="cluster-b",
                media_id=media_id,
                generation_id=generation_b,
            )
            catalog.link_cluster(link_after_reindex)

            links = catalog.clusters_for_person(record.person_id)
            self.assertEqual(len(links), 2)
            self.assertEqual(
                {link.cluster_id for link in links},
                {"cluster-a", "cluster-b"},
            )

            # The person record itself must be untouched by reindexing.
            self.assertEqual(catalog.get_person(record.person_id), record)

            # Lookup by the NEW cluster must resolve to the same person.
            found = catalog.person_for_cluster(
                cluster_id="cluster-b",
                media_id=media_id,
                generation_id=generation_b,
            )
            self.assertEqual(found, record)

    def test_unlink_cluster_corrects_an_accidental_merge_without_touching_media(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record()
            catalog.put_person(record)

            media_id = _uuid()
            generation_id = _uuid()
            link = cluster_link(
                person_id=record.person_id,
                cluster_id="cluster-accidental",
                media_id=media_id,
                generation_id=generation_id,
            )
            catalog.link_cluster(link)
            self.assertEqual(len(catalog.clusters_for_person(record.person_id)), 1)

            catalog.unlink_cluster(
                person_id=record.person_id,
                cluster_id="cluster-accidental",
                media_id=media_id,
                generation_id=generation_id,
            )
            self.assertEqual(catalog.clusters_for_person(record.person_id), ())
            # The person record itself remains; only the link was removed.
            self.assertEqual(catalog.get_person(record.person_id), record)

    def test_linking_the_same_cluster_twice_does_not_error(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record()
            catalog.put_person(record)
            link = cluster_link(
                person_id=record.person_id,
                cluster_id="cluster-a",
                media_id=_uuid(),
                generation_id=_uuid(),
            )
            catalog.link_cluster(link)
            catalog.link_cluster(link)  # must not raise
            self.assertEqual(len(catalog.clusters_for_person(record.person_id)), 1)

    def test_deleting_a_person_cascades_aliases_references_and_links_only(self):
        """Removing a reviewed identity must clean up its own aliases,
        references, and cluster links, but must never delete the
        underlying video/media/index data (Issue #85's explicit
        non-destructive removal requirement).
        """
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            record = person_record()
            catalog.put_person(record)
            catalog.add_alias(record.person_id, "AR")
            reference = person_reference(person_id=record.person_id)
            catalog.add_reference(reference)
            media_id = _uuid()
            generation_id = _uuid()
            catalog.link_cluster(
                cluster_link(
                    person_id=record.person_id,
                    cluster_id="cluster-a",
                    media_id=media_id,
                    generation_id=generation_id,
                )
            )

            catalog.delete_person(record.person_id)

            self.assertIsNone(catalog.get_person(record.person_id))
            self.assertEqual(catalog.list_aliases(record.person_id), ())
            self.assertEqual(catalog.list_references(record.person_id), ())
            self.assertEqual(catalog.clusters_for_person(record.person_id), ())


if __name__ == "__main__":
    unittest.main()
