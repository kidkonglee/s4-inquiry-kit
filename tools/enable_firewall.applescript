-- Requests authorization using the macOS system dialog. No password is collected by the project.
do shell script "/usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on && /usr/libexec/ApplicationFirewall/socketfilterfw --setstealthmode on" with administrator privileges
