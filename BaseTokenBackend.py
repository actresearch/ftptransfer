import time
import logging
import random
from portalocker import Lock
from portalocker.exceptions import LockException


from O365.utils import FileSystemTokenBackend

log = logging.getLogger(__name__)

class LockableFileSystemTokenBackend(FileSystemTokenBackend):
    """
    GH #350
    A token backend that ensures atomic operations when working with tokens
    stored on a file system. Avoids concurrent instances of O365 racing
    to refresh the same token file. It does this by wrapping the token refresh
    method in the Portalocker package's Lock class, which itself is a wrapper
    around Python's fcntl and win32con.
    """

    def __init__(self, *args, **kwargs):
        self.max_tries = kwargs.pop('max_tries')
        self.fs_wait = False
        super().__init__(*args, **kwargs)

    def should_refresh_token(self, con=None):
        """
        Method for refreshing the token when there are concurrently running
        O365 instances. Determines if we need to call the MS server and refresh
        the token and its file, or if another Connection instance has already
        updated it and we should just load that updated token from the file.

        It will always return False, None, OR raise an error if a token file
        couldn't be accessed after X tries. That is because this method
        completely handles token refreshing via the passed Connection object
        argument. If it determines that the token should be refreshed, it locks
        the token file, calls the Connection's 'refresh_token' method (which
        loads the fresh token from the server into memory and the file), then
        unlocks the file. Since refreshing has been taken care of, the calling
        method does not need to refresh and we return None.

        If we are blocked because the file is locked, that means another
        instance is using it. We'll change the backend's state to waiting,
        sleep for 2 seconds, reload a token into memory from the file (since
        another process is using it, we can assume it's being updated), and
        loop again.

        If this newly loaded token is not expired, the other instance loaded
        a new token to file, and we can happily move on and return False.
        (since we don't need to refresh the token anymore). If the same token
        was loaded into memory again and is still expired, that means it wasn't
        updated by the other instance yet. Try accessing the file again for X
        more times. If we don't suceed after the loop has terminated, raise a
        runtime exception
        """

        for _ in range(self.max_tries, 0, -1):
            if self.token.is_access_expired:
                try:
                    with Lock(self.token_path, 'r+',
                              fail_when_locked=True, timeout=0):
                        log.debug('Locked oauth token file')
                        if con.refresh_token() is False:
                            raise RuntimeError('Token Refresh Operation not '
                                               'working')
                        log.info('New oauth token fetched')
                    log.debug('Unlocked oauth token file')
                    return None
                except LockException:
                    self.fs_wait = True
                    log.warning('Oauth file locked. Sleeping for 2 seconds... retrying {} more times.'.format(_ - 1))
                    time.sleep(2)
                    log.debug('Waking up and rechecking token file for update'
                              ' from other instance...')
                    self.token = self.load_token()
            else:
                log.info('Token was refreshed by another instance...')
                self.fs_wait = False
                return False

        # if we exit the loop, that means we were locked out of the file after
        # multiple retries give up and throw an error - something isn't right
        raise RuntimeError('Could not access locked token file after {}'.format(self.max_tries))