// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

/// @notice MINIMAL LOCAL VERIFICATION SAMPLE ONLY (created 2026-10-16 with user approval).
/// Intentionally contains a classic reentrancy flaw (external call before state update)
/// and an unprotected self-destruct, solely so Slither's detectors can be exercised locally.
/// NOT production code. Do not deploy. No network/chain use intended.
contract ReentrancyVault {
    mapping(address => uint256) public balances;

    event Deposited(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount);

    function deposit() external payable {
        balances[msg.sender] += msg.value;
        emit Deposited(msg.sender, msg.value);
    }

    // Vulnerable: external call before state update (classic reentrancy).
    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount, "insufficient");
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
        balances[msg.sender] -= amount;
        emit Withdrawn(msg.sender, amount);
    }

    // Vulnerable: anyone can self-destruct this contract.
    function cleanup(address payable sink) external {
        selfdestruct(sink);
    }
}
