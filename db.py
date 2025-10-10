from sqlalchemy import create_engine
engine=create_engine('sqlite:///pioneer_helpdesk.db',echo=False)

def get_db():
    from sqlalchemy.orm import sessionmaker
    Session=sessionmaker(bind=engine)
    db=Session()
    try:
        yield db
    finally:
        db.close()
